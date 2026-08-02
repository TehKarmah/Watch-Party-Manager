"""Integration tests for /database manage's IMDb Metadata Recovery
workflow, exercised through the real bot.py wiring (show_imdb_recovery_
scope_selection, show_imdb_recovery_confirmation, start_imdb_recovery_
session) against real SuggestionService/ImdbMetadataService instances,
with lightweight fakes only for Discord objects -- mirroring the
project's established test convention (see test_imdb_metadata_refresh_
command.py). Pure business-logic coverage (discovery, search/matching,
merge/save-then-refresh, guild isolation at the data layer) lives in
test_imdb_metadata_recovery_service.py; this file covers the Discord-
facing scope/confirmation screens, permissions, per-suggestion Crew
Review (Accept/Skip/Search Again/Cancel Recovery), match selection,
post-sync, progress, the final summary, and idempotency at the command
level.

Unlike Refresh IMDb Metadata's single long-running interaction, every
recovery step is its own fresh interaction (a button click, a select, or
a modal submit) -- the _RecoveryScreen helper below threads a "current
screen" through a chain of clicks the same way, so each test reads as a
straight-line script of what a WASH Crew member would actually click.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import (
    handle_database_manage,
    show_imdb_recovery_confirmation,
    show_imdb_recovery_scope_selection,
)
from watch_party_manager.database_admin_view import CollectionManagementMenuView
from watch_party_manager.imdb_recovery_view import (
    RECOVERY_SCOPE_ALL_COLLECTIONS,
    RECOVERY_SCOPE_THIS_COLLECTION,
    ImdbRecoveryConfirmationView,
    ImdbRecoveryConfirmMatchView,
    ImdbRecoveryMatchSelectionView,
    ImdbRecoveryNoMatchView,
    ImdbRecoveryScopeSelectionView,
)
from watch_party_manager.domain.watch_item import MetadataProvider
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.services.imdb_metadata_service import ImdbMetadataService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_input_service import SuggestionInputService
from watch_party_manager.services.suggestion_service import SuggestionService

GUILD_ID = 100
OTHER_GUILD_ID = 200
CHANNEL_ID = 300
OTHER_GUILD_CHANNEL_ID = 301
WASH_CREW_ROLE_ID = 999
WATCH_PARTY_MEMBER_ROLE_ID = 555

ALIEN_SEARCH_MATCH = {"Title": "Alien", "Year": "1979", "imdbID": "tt0078748", "Type": "movie"}
ALIENS_SEARCH_MATCH = {"Title": "Aliens", "Year": "1986", "imdbID": "tt0090605", "Type": "movie"}
PREDATOR_SEARCH_MATCH = {"Title": "Predator", "Year": "1987", "imdbID": "tt0093773", "Type": "movie"}

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


def make_combined_fetch(*, search_responses: dict | None = None, title_responses: dict | None = None, calls: list | None = None):
    calls = calls if calls is not None else []
    search_responses = search_responses or {}
    title_responses = title_responses or {}

    def _fetch(url: str):
        calls.append(url)
        params = parse_qs(urlparse(url).query)
        if "s" in params:
            title = params["s"][0]
            year = params.get("y", [None])[0]
            return search_responses.get((title, year), {"Response": "False", "Error": "Movie not found!"})
        if "i" in params:
            imdb_id = params["i"][0]
            return title_responses.get(imdb_id, {"Response": "False", "Error": "Incorrect IMDb ID."})
        return {"Response": "False", "Error": "Bad request."}

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
        self.edited_attachments = "not-edited"
        self.sent_modal = None

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view

    async def edit_message(self, content=None, view=None, attachments=None) -> None:
        self.edited_content = content
        self.edited_view = view
        self.edited_attachments = attachments

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeInteraction:
    def __init__(self, *, guild_id=GUILD_ID, guild=None, roles=(WASH_CREW_ROLE_ID,)) -> None:
        self.user = FakeMember(roles=[FakeRole(role_id) for role_id in roles])
        self.guild_id = guild_id
        self.guild = guild
        self.response = FakeResponse()
        self.original_response_edits: list[dict] = []

    async def edit_original_response(self, *, content=None, view="unset", attachments=None) -> None:
        self.original_response_edits.append({"content": content, "view": view, "attachments": attachments})


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


class _RecoveryScreen:
    """Wraps whichever interaction most recently rendered a screen, so a
    test can keep clicking forward without caring whether that screen
    came from edit_original_response (only the very first screen after
    Start Recovery) or response.edit_message (every screen after).
    """

    def __init__(self, interaction: FakeInteraction, *, via_original: bool) -> None:
        self.interaction = interaction
        self._via_original = via_original

    @property
    def content(self) -> str:
        if self._via_original:
            return self.interaction.original_response_edits[-1]["content"]
        return self.interaction.response.edited_content

    @property
    def view(self):
        if self._via_original:
            return self.interaction.original_response_edits[-1]["view"]
        return self.interaction.response.edited_view

    @property
    def attachments(self):
        if self._via_original:
            return self.interaction.original_response_edits[-1]["attachments"]
        return self.interaction.response.edited_attachments

    def button(self, custom_id: str):
        return next(c for c in self.view.children if c.custom_id == custom_id)

    async def click(self, custom_id: str) -> "_RecoveryScreen":
        button = self.button(custom_id)
        new_interaction = FakeInteraction()
        await button.callback(interaction=new_interaction)
        return _RecoveryScreen(new_interaction, via_original=False)

    async def select_match(self, imdb_id: str) -> "_RecoveryScreen":
        select = next(c for c in self.view.children if isinstance(c, discord.ui.Select))
        select._values = [imdb_id]
        new_interaction = FakeInteraction()
        await select.callback(interaction=new_interaction)
        return _RecoveryScreen(new_interaction, via_original=False)

    async def search_again(self, query: str) -> "_RecoveryScreen":
        response_screen = await self.click("wpm_imdb_recovery_search_again")
        modal = response_screen.interaction.response.sent_modal
        modal.query_input._value = query
        modal_interaction = FakeInteraction()
        await modal.on_submit(interaction=modal_interaction)
        return _RecoveryScreen(modal_interaction, via_original=False)


class ImdbRecoveryCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        root = Path(self._temp_dir.name)
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.database = self.suggestion_service.create_database("Movies", guild_id=GUILD_ID, channel_id=CHANNEL_ID).database

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

    async def _reach_scope_selection(self, bot: FakeBot) -> FakeInteraction:
        interaction = FakeInteraction()
        await handle_database_manage(interaction, bot)
        select = interaction.response.sent_view.children[0]
        select._values = [str(self.database.database_id)]
        select_interaction = FakeInteraction()
        await select.callback(interaction=select_interaction)
        menu_view = select_interaction.response.edited_view
        recover_button = next(c for c in menu_view.children if c.custom_id == "wpm_database_manage_recover_imdb")

        scope_interaction = FakeInteraction()
        await recover_button.callback(interaction=scope_interaction)
        return scope_interaction

    async def _reach_confirmation(self, bot: FakeBot, scope: str) -> FakeInteraction:
        scope_interaction = await self._reach_scope_selection(bot)
        scope_view = scope_interaction.response.edited_view
        custom_id = (
            "wpm_imdb_recovery_scope_this_collection"
            if scope == RECOVERY_SCOPE_THIS_COLLECTION
            else "wpm_imdb_recovery_scope_all_collections"
        )
        button = next(c for c in scope_view.children if c.custom_id == custom_id)
        confirm_interaction = FakeInteraction()
        await button.callback(interaction=confirm_interaction)
        return confirm_interaction

    async def _start_recovery(self, bot: FakeBot, scope: str) -> _RecoveryScreen:
        confirm_interaction = await self._reach_confirmation(bot, scope)
        start_button = next(
            c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_recovery_start"
        )
        start_interaction = FakeInteraction()
        await start_button.callback(interaction=start_interaction)
        return _RecoveryScreen(start_interaction, via_original=True)


class ScopeSelectionTests(ImdbRecoveryCommandTestCase):
    async def test_offers_this_collection_all_collections_and_back(self) -> None:
        bot = self._bot()
        scope_interaction = await self._reach_scope_selection(bot)

        self.assertIsInstance(scope_interaction.response.edited_view, ImdbRecoveryScopeSelectionView)
        custom_ids = {c.custom_id for c in scope_interaction.response.edited_view.children}
        self.assertEqual(
            custom_ids,
            {"wpm_imdb_recovery_scope_this_collection", "wpm_imdb_recovery_scope_all_collections", "wpm_imdb_recovery_scope_back"},
        )

    async def test_back_returns_to_the_management_menu(self) -> None:
        bot = self._bot()
        scope_interaction = await self._reach_scope_selection(bot)
        back_button = next(c for c in scope_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_recovery_scope_back")

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, CollectionManagementMenuView)

    async def test_this_collection_reaches_confirmation_scoped_to_one_collection(self) -> None:
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        self.assertIsInstance(confirm_interaction.response.edited_view, ImdbRecoveryConfirmationView)
        self.assertIn("Recover This Collection", confirm_interaction.response.edited_content)
        self.assertIn("Movies", confirm_interaction.response.edited_content)

    async def test_all_collections_reaches_confirmation_scoped_to_every_active_collection(self) -> None:
        self.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_ALL_COLLECTIONS)

        self.assertIn("Recover All Collections", confirm_interaction.response.edited_content)
        self.assertIn("Collections: 2", confirm_interaction.response.edited_content)


class ConfirmationTests(ImdbRecoveryCommandTestCase):
    async def test_shows_scope_collection_count_and_missing_suggestion_count(self) -> None:
        self._add("Untitled")
        self._add("Alien", imdb_url="https://www.imdb.com/title/tt0078748/")
        bot = self._bot()

        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        content = confirm_interaction.response.edited_content
        self.assertIn("Collections: 1", content)
        self.assertIn("Suggestions missing a usable IMDb link: 1 (of 2 total considered)", content)

    async def test_discloses_external_requests_and_explicit_approval(self) -> None:
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        content = confirm_interaction.response.edited_content
        self.assertIn("OMDb", content)
        self.assertIn("explicitly", content)
        self.assertIn("never overwritten", content)
        self.assertIn("never changed", content)

    async def test_buttons_are_start_recovery_back_and_cancel(self) -> None:
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        custom_ids = {c.custom_id for c in confirm_interaction.response.edited_view.children}
        self.assertEqual(custom_ids, {"wpm_imdb_recovery_start", "wpm_imdb_recovery_confirm_back", "wpm_imdb_recovery_confirm_cancel"})

    async def test_back_returns_to_scope_selection(self) -> None:
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        back_button = next(c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_recovery_confirm_back")

        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)

        self.assertIsInstance(back_interaction.response.edited_view, ImdbRecoveryScopeSelectionView)

    async def test_cancel_makes_no_changes(self) -> None:
        self._add("Untitled")
        bot = self._bot()
        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        cancel_button = next(c for c in confirm_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_recovery_confirm_cancel")

        cancel_interaction = FakeInteraction()
        await cancel_button.callback(interaction=cancel_interaction)

        self.assertIn("cancelled", cancel_interaction.response.edited_content)
        self.assertIn("No changes were made", cancel_interaction.response.edited_content)

    async def test_no_external_request_before_start_recovery_is_clicked(self) -> None:
        self._add("Untitled")
        calls: list = []
        bot = self._bot(fetch_json=make_combined_fetch(calls=calls))

        await self._reach_confirmation(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        self.assertEqual(calls, [])


class DiscoveryTests(ImdbRecoveryCommandTestCase):
    async def test_a_suggestion_with_an_existing_imdb_link_is_never_offered(self) -> None:
        self._add("Alien", imdb_url="https://www.imdb.com/title/tt0078748/")
        bot = self._bot()

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        # Nothing missing -- straight to the final summary with 0 processed.
        self.assertIn("Suggestions processed: 0", screen.content)


class MatchingTests(ImdbRecoveryCommandTestCase):
    async def test_a_single_high_confidence_match_shows_a_confirmation_screen(self) -> None:
        self._add("Alien", release_year=1979)
        bot = self._bot(fetch_json=make_combined_fetch(search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}}))

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        self.assertIsInstance(screen.view, ImdbRecoveryConfirmMatchView)
        self.assertIn("Alien", screen.content)
        self.assertIn("Recovering 1 / 1", screen.content)

    async def test_multiple_plausible_matches_show_a_selection_list_with_distinguishing_info(self) -> None:
        self._add("Alien", release_year=1979)
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH, ALIENS_SEARCH_MATCH]}}
            )
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        self.assertIsInstance(screen.view, ImdbRecoveryMatchSelectionView)
        select = next(c for c in screen.view.children if isinstance(c, discord.ui.Select))
        option_values = {option.value for option in select.options}
        self.assertEqual(option_values, {"tt0078748", "tt0090605"})

    async def test_no_matches_offers_search_again_skip_and_cancel_only(self) -> None:
        self._add("Some Obscure Title", release_year=1978)
        bot = self._bot(fetch_json=make_combined_fetch())

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        self.assertIsInstance(screen.view, ImdbRecoveryNoMatchView)
        custom_ids = {c.custom_id for c in screen.view.children}
        self.assertEqual(custom_ids, {"wpm_imdb_recovery_search_again", "wpm_imdb_recovery_skip", "wpm_imdb_recovery_cancel"})
        self.assertNotIn("wpm_imdb_recovery_accept", custom_ids)

    async def test_year_mismatch_falls_back_and_flags_the_note(self) -> None:
        self._add("Alien", release_year=1978)
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("Alien", "1978"): {"Response": "False"}, ("Alien", None): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}}
            )
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)

        self.assertIsInstance(screen.view, ImdbRecoveryConfirmMatchView)
        self.assertIn("1978", screen.content)


class CrewReviewTests(ImdbRecoveryCommandTestCase):
    async def test_accept_saves_the_identifier_and_refreshes_metadata(self) -> None:
        item = self._add("Alien", release_year=1979)
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}},
                title_responses={"tt0078748": ALIEN_PAYLOAD},
            )
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        screen = await screen.click("wpm_imdb_recovery_accept")

        self.assertIn("Matched: 1", screen.content)
        updated = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(updated.director, "Ridley Scott")
        self.assertIn("Sigourney Weaver", updated.cast)

    async def test_skip_leaves_the_suggestion_unmatched_and_it_reappears_later(self) -> None:
        item = self._add("Alien", release_year=1979)
        bot = self._bot(fetch_json=make_combined_fetch(search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}}))

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        screen = await screen.click("wpm_imdb_recovery_skip")

        self.assertIn("Skipped: 1", screen.content)
        self.assertNotIn(MetadataProvider.IMDB, self.suggestion_service.get_suggestion(item.id).metadata_ids)
        # Re-running recovery finds it again -- not permanently suppressed.
        second_screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        self.assertIn("Recovering 1 / 1", second_screen.content)

    async def test_search_again_with_a_manual_title_and_year_re_searches(self) -> None:
        self._add("The Thing")
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("The Thing", None): {"Response": "False"}, ("The Thing", "1982"): {"Response": "True", "Search": [{"Title": "The Thing", "Year": "1982", "imdbID": "tt0084787", "Type": "movie"}]}}
            )
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        self.assertIsInstance(screen.view, ImdbRecoveryNoMatchView)
        screen = await screen.search_again("The Thing 1982")

        self.assertIsInstance(screen.view, ImdbRecoveryConfirmMatchView)
        self.assertIn("1982", screen.content)

    async def test_search_again_accepts_a_parenthesized_year(self) -> None:
        self._add("The Thing")
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("The Thing", None): {"Response": "False"}, ("The Thing", "1982"): {"Response": "True", "Search": [{"Title": "The Thing", "Year": "1982", "imdbID": "tt0084787", "Type": "movie"}]}}
            )
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        screen = await screen.search_again("The Thing (1982)")

        self.assertIsInstance(screen.view, ImdbRecoveryConfirmMatchView)

    async def test_selecting_a_candidate_from_the_list_still_requires_a_separate_accept(self) -> None:
        item = self._add("Alien", release_year=1979)
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH, ALIENS_SEARCH_MATCH]}},
                title_responses={"tt0090605": {"Response": "True", "Title": "Aliens", "Year": "1986"}},
            )
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        screen = await screen.select_match("tt0090605")

        self.assertIsInstance(screen.view, ImdbRecoveryConfirmMatchView)
        # Not yet saved -- selecting only proposes, Accept is still required.
        self.assertNotIn(MetadataProvider.IMDB, self.suggestion_service.get_suggestion(item.id).metadata_ids)
        screen = await screen.click("wpm_imdb_recovery_accept")
        self.assertIn("Matched: 1", screen.content)

    async def test_cancel_recovery_marks_all_remaining_suggestions_cancelled(self) -> None:
        self._add("Alien", release_year=1979)
        self._add("Predator", release_year=1987)
        bot = self._bot(fetch_json=make_combined_fetch(search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}, ("Predator", "1987"): {"Response": "True", "Search": [PREDATOR_SEARCH_MATCH]}}))

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        screen = await screen.click("wpm_imdb_recovery_cancel")

        self.assertIn("Cancelled: 2", screen.content)
        self.assertIn("Matched: 0", screen.content)

    async def test_a_technical_search_failure_that_is_skipped_counts_as_failed(self) -> None:
        self._add("Alien", release_year=1979)

        def _raising_fetch(url: str):
            raise RuntimeError("simulated network failure")

        bot = self._bot(fetch_json=_raising_fetch)

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        self.assertIsInstance(screen.view, ImdbRecoveryNoMatchView)
        screen = await screen.click("wpm_imdb_recovery_skip")

        self.assertIn("Failed: 1", screen.content)
        self.assertIn("Skipped: 0", screen.content)


class PostSyncIntegrationTests(ImdbRecoveryCommandTestCase):
    async def test_accepted_matchs_post_is_resynced(self) -> None:
        item = self._add("Alien", release_year=1979, channel_id=CHANNEL_ID)
        self.suggestion_service.set_confirmation_post_reference(item.id, GUILD_ID, CHANNEL_ID, 777)
        message = FakeMessage(message_id=777)
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}},
                title_responses={"tt0078748": ALIEN_PAYLOAD},
            ),
            channel=FakeChannel(message),
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        screen = await screen.click("wpm_imdb_recovery_accept")

        self.assertEqual(message.edit_calls, 1)
        self.assertIn("Suggestion posts updated: 1", screen.content)

    async def test_a_deleted_message_does_not_fail_the_whole_recovery(self) -> None:
        item = self._add("Alien", release_year=1979, channel_id=CHANNEL_ID)
        self.suggestion_service.set_confirmation_post_reference(item.id, GUILD_ID, CHANNEL_ID, 777)
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}},
                title_responses={"tt0078748": ALIEN_PAYLOAD},
            ),
            channel=FakeChannel(),  # message 777 not registered
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        screen = await screen.click("wpm_imdb_recovery_accept")

        self.assertEqual(self.suggestion_service.get_suggestion(item.id).director, "Ridley Scott")
        self.assertIn("Matched: 1", screen.content)
        self.assertIn("Suggestion post sync failures: 1", screen.content)


class ProgressAndSummaryTests(ImdbRecoveryCommandTestCase):
    async def test_final_summary_totals_are_correct(self) -> None:
        self._add("Alien", release_year=1979)
        self._add("Untitled Two")
        bot = self._bot(fetch_json=make_combined_fetch(search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}}))

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        screen = await screen.click("wpm_imdb_recovery_skip")  # Alien
        screen = await screen.click("wpm_imdb_recovery_skip")  # Untitled Two

        self.assertIn("Collections processed: 1", screen.content)
        self.assertIn("Suggestions processed: 2", screen.content)
        self.assertIn("Skipped: 2", screen.content)

    async def test_per_collection_breakdown_for_all_collections_scope(self) -> None:
        second = self.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self._add("Alien", release_year=1979)
        self._add("Predator", release_year=1987, database_id=second.database_id)
        bot = self._bot(fetch_json=make_combined_fetch())

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_ALL_COLLECTIONS)
        screen = await screen.click("wpm_imdb_recovery_skip")
        screen = await screen.click("wpm_imdb_recovery_skip")

        self.assertIn("Per-Collection Breakdown", screen.content)
        self.assertIn("Movies", screen.content)
        self.assertIn("TV Shows", screen.content)

    async def test_oversized_breakdown_is_attached_as_a_file(self) -> None:
        for index in range(40):
            self.suggestion_service.create_database(
                f"Collection With A Fairly Long Descriptive Name {index:03d}", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 10 + index
            )
        bot = self._bot()

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_ALL_COLLECTIONS)

        self.assertLessEqual(len(screen.content), 2000)
        self.assertIsNotNone(screen.attachments)
        self.assertIsInstance(screen.attachments[0], discord.File)


class IdempotencyIntegrationTests(ImdbRecoveryCommandTestCase):
    async def test_rerunning_after_a_match_no_longer_offers_that_suggestion(self) -> None:
        self._add("Alien", release_year=1979)
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}},
                title_responses={"tt0078748": ALIEN_PAYLOAD},
            )
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        await screen.click("wpm_imdb_recovery_accept")

        second_screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        self.assertIn("Suggestions processed: 0", second_screen.content)


class PermissionAndGuildIsolationTests(ImdbRecoveryCommandTestCase):
    async def test_non_wash_crew_member_cannot_reach_the_manage_menu_at_all(self) -> None:
        bot = self._bot()
        interaction = FakeInteraction(roles=(1,))

        await handle_database_manage(interaction, bot)

        self.assertIn("WASH Crew", interaction.response.sent_message)

    async def test_all_collections_never_includes_a_database_from_another_guild(self) -> None:
        self.suggestion_service.create_database("Other Guild Movies", guild_id=OTHER_GUILD_ID, channel_id=OTHER_GUILD_CHANNEL_ID)
        bot = self._bot()

        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_ALL_COLLECTIONS)

        self.assertIn("Collections: 1", confirm_interaction.response.edited_content)
        self.assertNotIn("Other Guild Movies", confirm_interaction.response.edited_content)

    async def test_all_collections_excludes_an_inactive_collection_in_the_same_guild(self) -> None:
        second = self.suggestion_service.create_database("Retired List", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self.suggestion_service.deactivate_database(second.database_id, GUILD_ID)
        bot = self._bot()

        confirm_interaction = await self._reach_confirmation(bot, RECOVERY_SCOPE_ALL_COLLECTIONS)

        self.assertIn("Collections: 1", confirm_interaction.response.edited_content)
        self.assertNotIn("Retired List", confirm_interaction.response.edited_content)

    async def test_recovering_all_collections_never_touches_a_suggestion_in_another_guild(self) -> None:
        other_database = self.suggestion_service.create_database(
            "Other Guild Movies", guild_id=OTHER_GUILD_ID, channel_id=OTHER_GUILD_CHANNEL_ID
        ).database
        other_item = self.suggestion_service.suggest("Alien", database_id=other_database.database_id, guild_id=OTHER_GUILD_ID).watch_item
        self._add("Predator", release_year=1987)
        bot = self._bot(fetch_json=make_combined_fetch(search_responses={("Predator", "1987"): {"Response": "True", "Search": [PREDATOR_SEARCH_MATCH]}}))

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_ALL_COLLECTIONS)
        screen = await screen.click("wpm_imdb_recovery_skip")

        untouched = self.suggestion_service.get_suggestion(other_item.id)
        self.assertEqual(untouched.title, "Alien")
        self.assertNotIn("Other Guild Movies", screen.content)


class RegressionTests(ImdbRecoveryCommandTestCase):
    async def test_every_other_database_manage_action_remains_reachable(self) -> None:
        bot = self._bot()
        scope_interaction = await self._reach_scope_selection(bot)
        back_button = next(c for c in scope_interaction.response.edited_view.children if c.custom_id == "wpm_imdb_recovery_scope_back")
        back_interaction = FakeInteraction()
        await back_button.callback(interaction=back_interaction)
        menu_view = back_interaction.response.edited_view

        custom_ids = {c.custom_id for c in menu_view.children}
        self.assertIn("wpm_database_manage_edit", custom_ids)
        self.assertIn("wpm_database_manage_refresh_imdb", custom_ids)
        self.assertIn("wpm_database_manage_reset", custom_ids)
        self.assertIn("wpm_database_manage_remove", custom_ids)

    async def test_recovery_never_changes_suggestion_status_or_id(self) -> None:
        item = self._add("Alien", release_year=1979)
        bot = self._bot(
            fetch_json=make_combined_fetch(
                search_responses={("Alien", "1979"): {"Response": "True", "Search": [ALIEN_SEARCH_MATCH]}},
                title_responses={"tt0078748": ALIEN_PAYLOAD},
            )
        )

        screen = await self._start_recovery(bot, RECOVERY_SCOPE_THIS_COLLECTION)
        await screen.click("wpm_imdb_recovery_accept")

        updated = self.suggestion_service.get_suggestion(item.id)
        self.assertEqual(updated.id, item.id)
        self.assertEqual(updated.status, item.status)
        self.assertEqual(updated.database_id, item.database_id)


if __name__ == "__main__":
    unittest.main()
