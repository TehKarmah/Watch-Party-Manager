"""Integration tests for /database manage's IMDb Metadata Refresh
workflow, exercised through the real bot.py wiring (show_imdb_refresh_
scope_selection, show_imdb_refresh_confirmation, perform_imdb_metadata_
refresh) against real SuggestionService/ImdbMetadataService instances,
with lightweight fakes only for Discord objects -- mirroring the
project's established test convention. Pure business-logic coverage
(dedup, merge, skip/fail semantics, guild isolation at the data layer)
lives in test_imdb_metadata_refresh_service.py; this file covers the
Discord-facing scope/confirmation screens, permissions, progress,
post-sync, the final summary, and idempotency at the command level.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import (
    IMDB_REFRESH_POST_EDIT_MIN_INTERVAL_SECONDS,
    handle_database_manage,
    perform_imdb_metadata_refresh,
    show_imdb_refresh_confirmation,
    show_imdb_refresh_scope_selection,
)
from watch_party_manager.database_admin_view import CollectionManagementMenuView
from watch_party_manager.imdb_refresh_view import (
    REFRESH_SCOPE_ALL_COLLECTIONS,
    REFRESH_SCOPE_THIS_COLLECTION,
    ImdbRefreshConfirmationView,
    ImdbRefreshScopeSelectionView,
)
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.suggestion_selection_view import DatabaseAdminSelectView

GUILD_ID = 100
OTHER_GUILD_ID = 200
CHANNEL_ID = 300
OTHER_GUILD_CHANNEL_ID = 301
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555

ALIEN_URL = "https://www.imdb.com/title/tt0078748/"
PREDATOR_URL = "https://www.imdb.com/title/tt0093773/"

ALIEN_PAYLOAD = {
    "Response": "True",
    "Title": "Alien",
    "Year": "1979",
    "Genre": "Horror, Sci-Fi",
    "Runtime": "117 min",
    "Rated": "R",
    "Director": "Ridley Scott",
    "imdbRating": "8.5",
    "Actors": "Sigourney Weaver, Tom Skerritt",
    "Poster": "https://example.com/alien.jpg",
    "Plot": "A crew finds a hostile alien.",
}
PREDATOR_PAYLOAD = {
    "Response": "True",
    "Title": "Predator",
    "Year": "1987",
    "Genre": "Action, Sci-Fi",
    "Rated": "R",
    "imdbRating": "7.8",
}


def make_fetch(responses: dict, *, calls: list | None = None):
    calls = calls if calls is not None else []

    def _fetch(url: str):
        calls.append(url)
        for imdb_id, payload in responses.items():
            if imdb_id in url:
                return payload
        return {"Response": "False", "Error": "Incorrect IMDb ID."}

    return _fetch


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
    def __init__(self, *, guild_id=GUILD_ID, guild=None, roles=(WASH_CREW_ROLE_ID,)) -> None:
        self.user = FakeMember(roles=[FakeRole(role_id) for role_id in roles])
        self.guild_id = guild_id
        self.guild = guild
        self.response = FakeResponse()
        self.original_response_edits: list[dict] = []

    async def edit_original_response(self, *, content=None, attachments=None) -> None:
        self.original_response_edits.append({"content": content, "attachments": attachments})


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edited_embed = None
        self.edit_calls = 0

    async def edit(self, *, embed=None, view=None) -> None:
        self.edited_embed = embed
        self.edit_calls += 1


class FakeChannel:
    def __init__(self, *messages: FakeMessage) -> None:
        self._messages = {message.id: message for message in messages}

    async def fetch_message(self, message_id):
        return self._messages[message_id]


class FakeGuildConfigurationRepository:
    def get(self, guild_id):
        return None


class FakeBot:
    def __init__(
        self,
        suggestion_service: SuggestionService,
        *,
        fetch_json=None,
        channel: FakeChannel | None = None,
        wash_crew_role_id=WASH_CREW_ROLE_ID,
    ) -> None:
        self.suggestion_service = suggestion_service
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            Path(tempfile.mkdtemp()) / "suggestion_database_configurations.json"
        )
        self.guild_configuration_repository = FakeGuildConfigurationRepository()
        self.wash_crew_role_id = wash_crew_role_id
        self.suggestion_input_service = SuggestionInputService(
            ImdbMetadataService(api_key="test-key", fetch_json=fetch_json or (lambda u: {}))
        )
        self.permission_service = PermissionService(
            watch_party_member_role_id=WATCH_PARTY_MEMBER_ROLE_ID, wash_crew_role_id=wash_crew_role_id
        )
        self.vote_service = None
        self._channel = channel

    def get_channel(self, channel_id):
        return self._channel

    async def fetch_channel(self, channel_id):
        return self._channel


class ImdbRefreshCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database(
            "Movies", guild_id=GUILD_ID, channel_id=CHANNEL_ID
        ).database

    def _add(self, title: str, *, imdb_url=None, database_id=None, **kwargs):
        return self.suggestion_service.suggest(
            title,
            imdb_url=imdb_url,
            database_id=database_id if database_id is not None else self.database.database_id,
            guild_id=GUILD_ID,
            **kwargs,
        ).watch_item

    def _bot(self, *, fetch_json=None, channel=None, wash_crew_role_id=WASH_CREW_ROLE_ID) -> FakeBot:
        return FakeBot(self.suggestion_service, fetch_json=fetch_json, channel=channel, wash_crew_role_id=wash_crew_role_id)

    async def _reach_scope_selection(self, bot: FakeBot):
        interaction = FakeInteraction()
        await handle_database_manage(interaction, bot)
        select = interaction.response.sent_view.children[0]
        select._values = [str(self.database.database_id)]
        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)
        menu_view = select_interaction.response.edited_view
        refresh_button = next(c for c in menu_view.children if c.custom_id == "wpm_database_manage_refresh_imdb")

        scope_interaction = FakeInteraction()
        await refresh_button.callback(interaction=scope_interaction)
        return scope_interaction

    async def _reach_confirmation(self, bot: FakeBot, scope: str):
        scope_interaction = await self._reach_scope_selection(bot)
        scope_view = scope_interaction.response.edited_view
        custom_id = (
            "wpm_imdb_refresh_scope_this_collection"
            if scope == REFRESH_SCOPE_THIS_COLLECTION
            else "wpm_imdb_refresh_scope_all_collections"
        )
        button = next(c for c in scope_view.children if c.custom_id == custom_id)
        confirm_interaction = FakeInteraction()
        await button.callback(interaction=confirm_interaction)
        return confirm_interaction


class ScopeSelectionTests(ImdbRefreshCommandTestCase):
    async def test_offers_this_collection_all_collections_and_back(self) -> None:
        bot = self._bot()
        scope_interaction = await self._reach_scope_selection(bot)

        self.assertIsInstance(scope_interaction.response.edited_view, ImdbRefreshScopeSelectionView)
        custom_ids = {c.custom_id for c in scope_interaction.response.edited_view.children}
        self.assertEqual(
            custom_ids,
            {
                "wpm_imdb_refresh_scope_this_collection",
                "wpm_imdb_refresh_scope_all_collections",
                "wpm_imdb_refresh_scope_back",
            },
        )

    async def test_both_scopes_are_shown_even_with_only_one_active_collection(self) -> None:
        bot = self._bot()
        scope_interaction = await self._reach_scope_selection(bot)

        labels = {c.custom_id: c.label for c in scope_interaction.response.edited_view.children}
        self.assertEqual(labels["wpm_imdb_refresh_scope_this_collection"], "Refresh This Collection")
        self.assertEqual(labels["wpm_imdb_refresh_scope_all_collections"], "Refresh All Collections")

    async def test_back_returns_to_the_management_menu(self) -> None:
        bot = self._bot()
        scope_interaction = await self._reach_scope_selection(bot)
        back_button = next(
            c for c in scope_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_scope_back"
        )

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, CollectionManagementMenuView)

    async def test_this_collection_reaches_confirmation_scoped_to_one_collection(self) -> None:
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)

        self.assertIsInstance(confirm_interaction.response.edited_view, ImdbRefreshConfirmationView)
        self.assertIn("Refresh This Collection", confirm_interaction.response.edited_content)
        self.assertIn("Movies", confirm_interaction.response.edited_content)

    async def test_all_collections_reaches_confirmation_scoped_to_every_active_collection(self) -> None:
        self.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_ALL_COLLECTIONS)

        self.assertIn("Refresh All Collections", confirm_interaction.response.edited_content)
        self.assertIn("Collections: 2", confirm_interaction.response.edited_content)


class ConfirmationTests(ImdbRefreshCommandTestCase):
    async def test_shows_scope_collection_count_and_eligible_suggestion_count(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Untitled")
        bot = self._bot()

        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)

        content = confirm_interaction.response.edited_content
        self.assertIn("Collections: 1", content)
        self.assertIn("Suggestions eligible for refresh: 1 (of 2 total considered)", content)

    async def test_discloses_external_requests_and_history_preservation(self) -> None:
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)

        content = confirm_interaction.response.edited_content
        self.assertIn("IMDb/OMDb requests", content)
        self.assertIn("never changed", content)
        self.assertIn("may take some time", content)

    async def test_buttons_are_start_refresh_back_and_cancel(self) -> None:
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)

        custom_ids = {c.custom_id for c in confirm_interaction.response.edited_view.children}
        self.assertEqual(
            custom_ids, {"wpm_imdb_refresh_start", "wpm_imdb_refresh_confirm_back", "wpm_imdb_refresh_confirm_cancel"}
        )

    async def test_back_returns_to_scope_selection(self) -> None:
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)
        back_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_confirm_back"
        )

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, ImdbRefreshScopeSelectionView)

    async def test_cancel_makes_no_changes_and_confirms_nothing_happened(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)
        cancel_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_confirm_cancel"
        )

        cancel_interaction = FakeInteraction()
        await cancel_button.callback(interaction=cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.edited_content)
        self.assertIn("No changes were made", cancel_interaction.response.edited_content)
        # No external request was ever made and nothing changed.
        self.assertEqual(self.suggestion_service.get_suggestions_for_database(self.database.database_id)[0].title, "Alien")

    async def test_no_external_request_before_start_refresh_is_clicked(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        calls: list = []
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}, calls=calls))

        await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)

        self.assertEqual(calls, [])


class PermissionAndGuildIsolationTests(ImdbRefreshCommandTestCase):
    async def test_non_wash_crew_member_cannot_reach_the_manage_menu_at_all(self) -> None:
        bot = self._bot()
        interaction = FakeInteraction(roles=(1,))

        await handle_database_manage(interaction, bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_all_collections_never_includes_a_database_from_another_guild(self) -> None:
        self.suggestion_service.create_database("Other Guild Movies", guild_id=OTHER_GUILD_ID, channel_id=OTHER_GUILD_CHANNEL_ID)
        bot = self._bot()

        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_ALL_COLLECTIONS)

        # Only this guild's one collection is counted.
        self.assertIn("Collections: 1", confirm_interaction.response.edited_content)
        self.assertNotIn("Other Guild Movies", confirm_interaction.response.edited_content)

    async def test_all_collections_excludes_an_inactive_collection_in_the_same_guild(self) -> None:
        second = self.suggestion_service.create_database("Retired List", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self.suggestion_service.deactivate_database(second.database_id, GUILD_ID)
        bot = self._bot()

        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_ALL_COLLECTIONS)

        self.assertIn("Collections: 1", confirm_interaction.response.edited_content)
        self.assertNotIn("Retired List", confirm_interaction.response.edited_content)

    async def test_refreshing_all_collections_never_touches_a_suggestion_in_another_guild(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other Guild Movies", guild_id=OTHER_GUILD_ID, channel_id=OTHER_GUILD_CHANNEL_ID
        ).database
        other_item = self.suggestion_service.suggest(
            "Alien", imdb_url=ALIEN_URL, database_id=other_database.database_id, guild_id=OTHER_GUILD_ID
        ).watch_item
        self._add("Predator", imdb_url=PREDATOR_URL)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD, "tt0093773": PREDATOR_PAYLOAD}))

        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_ALL_COLLECTIONS)
        start_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_start"
        )
        start_interaction = FakeInteraction()
        await start_button.callback(interaction=start_interaction)

        # The other guild's suggestion is completely untouched.
        untouched = self.suggestion_service.get_suggestion(other_item.id)
        self.assertEqual(untouched.title, "Alien")
        self.assertIsNone(untouched.cast and None or untouched.director)  # never enriched
        summary_text = start_interaction.original_response_edits[-1]["content"]
        self.assertNotIn("Other Guild Movies", summary_text)


class ExecutionProgressAndSummaryTests(ImdbRefreshCommandTestCase):
    async def _start_refresh(self, bot: FakeBot, scope: str) -> FakeInteraction:
        confirm_interaction = await self._reach_confirmation(bot, scope)
        start_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_start"
        )
        start_interaction = FakeInteraction()
        await start_button.callback(interaction=start_interaction)
        return start_interaction

    async def test_acknowledges_immediately_via_a_separate_message_edit_from_progress(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}))

        start_interaction = await self._start_refresh(bot, REFRESH_SCOPE_THIS_COLLECTION)

        self.assertIn("Starting", start_interaction.response.edited_content)
        self.assertIsNone(start_interaction.response.edited_view)

    async def test_progress_updates_are_bounded_not_one_per_suggestion(self) -> None:
        # Every lookup must succeed on the first attempt here -- a
        # failure would trigger the refresh service's own bounded retry
        # (a real, short asyncio.sleep), and enough of those stacked up
        # across several suggestions would eventually cross the progress
        # throttle's interval for reasons unrelated to what this test is
        # actually checking.
        for index in range(5):
            self._add(f"Movie {index}", imdb_url=f"https://www.imdb.com/title/tt{index:07d}/")
        bot = self._bot(fetch_json=lambda url: {"Response": "True", "Title": "Some Movie", "Year": "2020"})

        start_interaction = await self._start_refresh(bot, REFRESH_SCOPE_THIS_COLLECTION)

        # One collection, five suggestions processed in well under the
        # progress throttle's interval -> exactly one collection-boundary
        # progress edit, then one final summary edit -- never 5 (one per
        # suggestion).
        self.assertEqual(len(start_interaction.original_response_edits), 2)

    async def test_final_summary_totals_are_correct(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Untitled")
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}))

        start_interaction = await self._start_refresh(bot, REFRESH_SCOPE_THIS_COLLECTION)

        summary_text = start_interaction.original_response_edits[-1]["content"]
        self.assertIn("Collections processed: 1", summary_text)
        self.assertIn("Suggestions processed: 2", summary_text)
        self.assertIn("Refreshed: 1", summary_text)
        self.assertIn("Skipped: 1", summary_text)
        self.assertIn("Failed: 0", summary_text)

    async def test_per_collection_breakdown_included_for_all_collections_scope(self) -> None:
        second = self.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self._add("Alien", imdb_url=ALIEN_URL)
        self._add("Predator", imdb_url=PREDATOR_URL, database_id=second.database_id)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD, "tt0093773": PREDATOR_PAYLOAD}))

        start_interaction = await self._start_refresh(bot, REFRESH_SCOPE_ALL_COLLECTIONS)

        summary_text = start_interaction.original_response_edits[-1]["content"]
        self.assertIn("Per-Collection Breakdown", summary_text)
        self.assertIn("Movies", summary_text)
        self.assertIn("TV Shows", summary_text)

    async def test_no_per_collection_breakdown_for_a_single_collection_scope(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}))

        start_interaction = await self._start_refresh(bot, REFRESH_SCOPE_THIS_COLLECTION)

        summary_text = start_interaction.original_response_edits[-1]["content"]
        self.assertNotIn("Per-Collection Breakdown", summary_text)

    async def test_oversized_breakdown_is_attached_as_a_file_instead_of_truncated_inline(self) -> None:
        for index in range(40):
            self.suggestion_service.create_database(
                f"Collection With A Fairly Long Descriptive Name {index:03d}", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 10 + index
            )
        bot = self._bot()

        start_interaction = await self._start_refresh(bot, REFRESH_SCOPE_ALL_COLLECTIONS)

        final_edit = start_interaction.original_response_edits[-1]
        self.assertLessEqual(len(final_edit["content"]), 2000)
        self.assertIsNotNone(final_edit["attachments"])
        attachment = final_edit["attachments"][0]
        self.assertIsInstance(attachment, discord.File)


class PostSyncIntegrationTests(ImdbRefreshCommandTestCase):
    async def test_refreshed_suggestions_own_post_is_resynced(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL, channel_id=CHANNEL_ID)
        self.suggestion_service.set_confirmation_post_reference(item.id, GUILD_ID, CHANNEL_ID, 777)
        message = FakeMessage(message_id=777)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}), channel=FakeChannel(message))

        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)
        start_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_start"
        )
        start_interaction = FakeInteraction()
        await start_button.callback(interaction=start_interaction)

        self.assertEqual(message.edit_calls, 1)
        summary_text = start_interaction.original_response_edits[-1]["content"]
        self.assertIn("Suggestion posts updated: 1", summary_text)
        self.assertIn("Suggestion post sync failures: 0", summary_text)

    async def test_skipped_suggestion_never_triggers_a_post_edit(self) -> None:
        item = self._add("Untitled", channel_id=CHANNEL_ID)
        self.suggestion_service.set_confirmation_post_reference(item.id, GUILD_ID, CHANNEL_ID, 777)
        message = FakeMessage(message_id=777)
        bot = self._bot(channel=FakeChannel(message))

        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)
        start_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_start"
        )
        start_interaction = FakeInteraction()
        await start_button.callback(interaction=start_interaction)

        self.assertEqual(message.edit_calls, 0)

    async def test_a_deleted_message_does_not_fail_the_whole_refresh(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL, channel_id=CHANNEL_ID)
        self.suggestion_service.set_confirmation_post_reference(item.id, GUILD_ID, CHANNEL_ID, 777)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}), channel=FakeChannel())  # no message 777 registered

        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)
        start_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_start"
        )
        start_interaction = FakeInteraction()
        await start_button.callback(interaction=start_interaction)

        # The metadata update itself still succeeded.
        self.assertEqual(self.suggestion_service.get_suggestion(item.id).director, "Ridley Scott")
        summary_text = start_interaction.original_response_edits[-1]["content"]
        self.assertIn("Refreshed: 1", summary_text)
        self.assertIn("Suggestion post sync failures: 1", summary_text)


class MultiChannelFakeBot(FakeBot):
    """A FakeBot variant routing get_channel/fetch_channel by channel id,
    for pacing tests that need suggestion posts to live in more than one
    channel at once (FakeBot's single `channel` kwarg only models one).
    """

    def __init__(self, suggestion_service, *, fetch_json=None, channels: dict[int, "FakeChannel"] = None) -> None:
        super().__init__(suggestion_service, fetch_json=fetch_json)
        self._channels_by_id = channels or {}

    def get_channel(self, channel_id):
        return self._channels_by_id.get(channel_id)

    async def fetch_channel(self, channel_id):
        return self._channels_by_id.get(channel_id)


class PostEditPacingTests(ImdbRefreshCommandTestCase):
    """Rate-limit friendliness: perform_imdb_metadata_refresh must pace
    suggestion-post edits landing in the same channel, without waiting in
    real time during tests (a fake `sleep` records requested durations
    instead of actually waiting) and without pacing edits to different
    channels against each other.
    """

    async def test_pacing_waits_between_edits_to_the_same_channel(self) -> None:
        first = self._add("Alien", imdb_url=ALIEN_URL, channel_id=CHANNEL_ID)
        second = self._add("Predator", imdb_url=PREDATOR_URL, channel_id=CHANNEL_ID)
        self.suggestion_service.set_confirmation_post_reference(first.id, GUILD_ID, CHANNEL_ID, 777)
        self.suggestion_service.set_confirmation_post_reference(second.id, GUILD_ID, CHANNEL_ID, 778)
        message_a = FakeMessage(message_id=777)
        message_b = FakeMessage(message_id=778)
        bot = self._bot(
            fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD, "tt0093773": PREDATOR_PAYLOAD}),
            channel=FakeChannel(message_a, message_b),
        )
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        start_interaction = FakeInteraction()
        await perform_imdb_metadata_refresh(start_interaction, bot, [self.database], sleep=fake_sleep)

        self.assertEqual(message_a.edit_calls, 1)
        self.assertEqual(message_b.edit_calls, 1)
        # The second suggestion's post edit landed in the same channel
        # immediately after the first (no real time elapsed, since
        # fake_sleep never actually waits) -- pacing must have kicked in
        # exactly once, for close to the full configured interval.
        self.assertEqual(len(sleep_calls), 1)
        self.assertAlmostEqual(sleep_calls[0], IMDB_REFRESH_POST_EDIT_MIN_INTERVAL_SECONDS, delta=0.1)

    async def test_no_pacing_wait_across_different_channels(self) -> None:
        first = self._add("Alien", imdb_url=ALIEN_URL, channel_id=CHANNEL_ID)
        second = self._add("Predator", imdb_url=PREDATOR_URL, channel_id=CHANNEL_ID + 1)
        self.suggestion_service.set_confirmation_post_reference(first.id, GUILD_ID, CHANNEL_ID, 777)
        self.suggestion_service.set_confirmation_post_reference(second.id, GUILD_ID, CHANNEL_ID + 1, 778)
        message_a = FakeMessage(message_id=777)
        message_b = FakeMessage(message_id=778)
        bot = MultiChannelFakeBot(
            self.suggestion_service,
            fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD, "tt0093773": PREDATOR_PAYLOAD}),
            channels={CHANNEL_ID: FakeChannel(message_a), CHANNEL_ID + 1: FakeChannel(message_b)},
        )
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        start_interaction = FakeInteraction()
        await perform_imdb_metadata_refresh(start_interaction, bot, [self.database], sleep=fake_sleep)

        self.assertEqual(message_a.edit_calls, 1)
        self.assertEqual(message_b.edit_calls, 1)
        self.assertEqual(sleep_calls, [])

    async def test_a_suggestion_with_no_existing_post_never_incurs_a_pacing_wait(self) -> None:
        with_post = self._add("Alien", imdb_url=ALIEN_URL, channel_id=CHANNEL_ID)
        self.suggestion_service.set_confirmation_post_reference(with_post.id, GUILD_ID, CHANNEL_ID, 777)
        self._add("Untitled")  # no IMDb link -- Skipped, never even considered for post-sync.
        message = FakeMessage(message_id=777)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}), channel=FakeChannel(message))
        sleep_calls: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        start_interaction = FakeInteraction()
        await perform_imdb_metadata_refresh(start_interaction, bot, [self.database], sleep=fake_sleep)

        self.assertEqual(message.edit_calls, 1)
        self.assertEqual(sleep_calls, [])

    async def test_summary_totals_and_persistence_are_unaffected_by_pacing(self) -> None:
        first = self._add("Alien", imdb_url=ALIEN_URL, channel_id=CHANNEL_ID)
        second = self._add("Predator", imdb_url=PREDATOR_URL, channel_id=CHANNEL_ID)
        self.suggestion_service.set_confirmation_post_reference(first.id, GUILD_ID, CHANNEL_ID, 777)
        self.suggestion_service.set_confirmation_post_reference(second.id, GUILD_ID, CHANNEL_ID, 778)
        bot = self._bot(
            fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD, "tt0093773": PREDATOR_PAYLOAD}),
            channel=FakeChannel(FakeMessage(message_id=777), FakeMessage(message_id=778)),
        )

        async def fake_sleep(seconds: float) -> None:
            return None

        start_interaction = FakeInteraction()
        await perform_imdb_metadata_refresh(start_interaction, bot, [self.database], sleep=fake_sleep)

        summary_text = start_interaction.original_response_edits[-1]["content"]
        self.assertIn("Refreshed: 2", summary_text)
        self.assertIn("Suggestion posts updated: 2", summary_text)
        self.assertIn("Suggestion post sync failures: 0", summary_text)
        self.assertEqual(self.suggestion_service.get_suggestion(first.id).director, "Ridley Scott")


class IdempotencyIntegrationTests(ImdbRefreshCommandTestCase):
    async def test_rerunning_the_full_command_flow_produces_unchanged_the_second_time(self) -> None:
        self._add("Alien", imdb_url=ALIEN_URL)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}))

        async def run_once():
            confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)
            start_button = next(
                c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_start"
            )
            start_interaction = FakeInteraction()
            await start_button.callback(interaction=start_interaction)
            return start_interaction.original_response_edits[-1]["content"]

        first_summary = await run_once()
        second_summary = await run_once()

        self.assertIn("Refreshed: 1", first_summary)
        self.assertIn("Refreshed: 0", second_summary)
        self.assertIn("Unchanged: 1", second_summary)
        self.assertEqual(len(self.suggestion_service.get_suggestions_for_database(self.database.database_id)), 1)


class RegressionTests(ImdbRefreshCommandTestCase):
    async def test_every_other_database_manage_action_remains_reachable(self) -> None:
        bot = self._bot()
        scope_interaction = await self._reach_scope_selection(bot)
        back_button = next(
            c for c in scope_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_scope_back"
        )
        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)
        menu_view = back_interaction.response.edited_view

        custom_ids = {c.custom_id for c in menu_view.children}
        self.assertIn("wpm_database_manage_edit", custom_ids)
        self.assertIn("wpm_database_manage_backup", custom_ids)
        self.assertIn("wpm_database_manage_move", custom_ids)
        self.assertIn("wpm_database_manage_reset", custom_ids)
        self.assertIn("wpm_database_manage_remove", custom_ids)

    async def test_refreshing_metadata_never_changes_suggestion_status_or_id(self) -> None:
        item = self._add("Alien", imdb_url=ALIEN_URL)
        bot = self._bot(fetch_json=make_fetch({"tt0078748": ALIEN_PAYLOAD}))

        confirm_interaction = await self._reach_confirmation(bot, REFRESH_SCOPE_THIS_COLLECTION)
        start_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_refresh_start"
        )
        start_interaction = FakeInteraction()
        await start_button.callback(interaction=start_interaction)

        updated = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(updated.id, item.id)
        self.assertEqual(updated.status, item.status)
        self.assertEqual(updated.database_id, item.database_id)


if __name__ == "__main__":
    unittest.main()
