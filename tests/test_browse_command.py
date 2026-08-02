"""Integration tests for /browse -- WASH's interactive collection
browser. Exercises the real bot.py wiring (handle_browse,
show_browse_collection_picker, send_browse_session, perform_browse_
random_pick, perform_browse_post_publicly) against real SuggestionService/
CollectionEligibilityService/VoteService/NomineeSelectionService/
PermissionService instances, with lightweight fakes only for Discord
objects -- mirroring the project's established test convention (see
test_random_watch_command.py, test_start_vote_flow.py).

/browse now opens the shared filter menu first -- View Results is what
actually builds and shows the paginated results screen (Filter-Menu-First
UX refinement). _browse() below reaches the filter menu (or the
collection picker, when ambiguous); _browse_to_results() additionally
clicks View Results, for tests that only care about the results screen
itself, which is otherwise unchanged.

Pure, Discord-free formatting/revalidation logic is covered separately
in test_browse_service.py.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import handle_browse, show_browse_collection_picker
from watch_party_manager.browse_view import BrowsePublicResultsView
from watch_party_manager.filter_menu_view import FILTER_CATEGORY_GENRE, FILTER_CATEGORY_IMDB_RATING, FilterMenuView
from watch_party_manager.pagination_view import PaginatedListView
from watch_party_manager.persistence.guild_configuration_repository import GuildConfigurationRepository
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.random_watch_view import RandomWatchResultView
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService
from watch_party_manager.start_vote_view import CustomizeVoteOverridesView

GUILD_ID = 100
OTHER_GUILD_ID = 200
CHANNEL_ID = 300
OTHER_CHANNEL_ID = 301
WASH_CREW_ROLE_ID = 900
WATCH_PARTY_ROLE_ID = 800

VIEW_RESULTS_CUSTOM_ID = "wpm_browse_filter_menu_view_results"


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, *, roles=()) -> None:
        self.id = user_id
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.sent_embed = None
        self.sent_modal = None
        self.edited_content = None
        self.edited_view = "not-edited"
        self.edited_embed = "not-edited"
        self.deferred = False

    async def send_message(self, content=None, *, ephemeral=False, view=None, embed=None, suppress_embeds=False) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view
        self.sent_embed = embed

    async def edit_message(self, content=None, view=None, embed=None, suppress_embeds=False) -> None:
        self.edited_content = content
        self.edited_view = view
        self.edited_embed = embed

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal

    async def defer(self, *, ephemeral=False, thinking=False) -> None:
        self.deferred = True


class FakeFollowup:
    def __init__(self) -> None:
        self.sent_content = None
        self.sent_view = None
        self.sent_embed = None
        self.sent_ephemeral = None

    async def send(self, content=None, *, embed=None, view=None, ephemeral=False) -> None:
        self.sent_content = content
        self.sent_embed = embed
        self.sent_view = view
        self.sent_ephemeral = ephemeral


class FakeInteraction:
    def __init__(self, *, user=None, guild_id=GUILD_ID, channel_id=CHANNEL_ID, guild=None, roles=(WASH_CREW_ROLE_ID,)) -> None:
        self.user = user if user is not None else FakeMember(1, roles=[FakeRole(r) for r in roles])
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.guild = guild
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class FakeSchedulerHost:
    def __init__(self) -> None:
        self.scheduler_service = None


class FakeBot:
    def __init__(self, root: Path, *, wash_crew_role_id=WASH_CREW_ROLE_ID, watch_party_role_id=WATCH_PARTY_ROLE_ID) -> None:
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(self.suggestion_service, repository=JsonVoteRepository(root / "voting.json"))
        self.nominee_selection_service = NomineeSelectionService(self.suggestion_service, self.vote_service)
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.guild_configuration_repository = GuildConfigurationRepository(root / "guild_configurations.json")
        self.collection_eligibility_service = CollectionEligibilityService(self.suggestion_service, self.vote_service)
        self.permission_service = PermissionService(
            watch_party_member_role_id=watch_party_role_id, wash_crew_role_id=wash_crew_role_id
        )
        self.wash_crew_role_id = wash_crew_role_id
        self.default_nominee_count = 3
        self.scheduler_host = FakeSchedulerHost()


class BrowseCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.root = Path(self._temp_dir.name)
        self.bot = FakeBot(self.root)
        self.database = self.bot.suggestion_service.create_database("Movies", guild_id=GUILD_ID, channel_id=CHANNEL_ID).database

    def _add(self, title: str, database_id=None, **kwargs):
        result = self.bot.suggestion_service.suggest(
            title, database_id=database_id if database_id is not None else self.database.database_id, guild_id=GUILD_ID, **kwargs
        )
        self.assertTrue(result.success, result.message)
        return result.watch_item

    def _member(self, user_id: int, *, watch_party=True, crew=False) -> FakeMember:
        roles = []
        if watch_party:
            roles.append(FakeRole(WATCH_PARTY_ROLE_ID))
        if crew:
            roles.append(FakeRole(WASH_CREW_ROLE_ID))
        return FakeMember(user_id, roles=roles)

    async def _browse(self, *, crew: bool = True) -> FakeInteraction:
        """Reach /browse's entry screen -- the shared filter menu for a
        single/auto-resolved collection, or the collection picker when
        ambiguous.
        """
        interaction = FakeInteraction(user=self._member(1, crew=crew))
        await handle_browse(interaction, self.bot)
        return interaction

    def _screen_view(self, interaction: FakeInteraction):
        return interaction.response.sent_view if interaction.response.sent_view is not None else interaction.response.edited_view

    def _content(self, interaction: FakeInteraction) -> str:
        return interaction.response.sent_message if interaction.response.sent_message is not None else interaction.response.edited_content

    async def _click(self, view, custom_id: str, *, crew: bool = True) -> FakeInteraction:
        button = next(c for c in view.children if getattr(c, "custom_id", None) == custom_id)
        interaction = FakeInteraction(user=self._member(1, crew=crew))
        await button.callback(interaction=interaction)
        return interaction

    async def _view_results(self, filter_menu_interaction: FakeInteraction, *, crew: bool = True) -> FakeInteraction:
        """Click View Results on a filter-menu screen, landing on the
        paginated (or empty-state) results screen.
        """
        menu_view = self._screen_view(filter_menu_interaction)
        return await self._click(menu_view, VIEW_RESULTS_CUSTOM_ID, crew=crew)

    async def _browse_to_results(self, *, crew: bool = True) -> FakeInteraction:
        """/browse -> filter menu -> View Results, for tests that only
        care about the (otherwise unchanged) results screen itself.
        """
        menu_interaction = await self._browse(crew=crew)
        return await self._view_results(menu_interaction, crew=crew)

    async def _set_genre_filter(self, filter_menu_interaction: FakeInteraction, genre: str) -> FakeInteraction:
        """Navigate Filter Menu -> Genre -> select genre -> back to the
        (refreshed) filter menu. Returns the interaction whose
        edited_view is the refreshed FilterMenuView.
        """
        menu_view = self._screen_view(filter_menu_interaction)
        menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_interaction = FakeInteraction(user=self._member(1))
        await menu_view.children[0].callback(interaction=genre_edit_interaction)
        genre_edit_view = genre_edit_interaction.response.edited_view
        genre_select = genre_edit_view.children[0]
        genre_select._values = [genre]
        genre_set_interaction = FakeInteraction(user=self._member(1))
        await genre_select.callback(interaction=genre_set_interaction)
        return genre_set_interaction


class EntryFlowTests(BrowseCommandTestCase):
    async def test_single_collection_begins_directly_at_the_filter_menu(self) -> None:
        self._add("Alien")

        interaction = await self._browse()

        self.assertIsInstance(self._screen_view(interaction), FilterMenuView)

    async def test_filter_menu_names_the_resolved_collection(self) -> None:
        self._add("Alien")

        interaction = await self._browse()

        self.assertIn("Movies", self._content(interaction))

    async def test_results_are_not_shown_until_view_results_is_clicked(self) -> None:
        self._add("Alien")

        interaction = await self._browse()

        content = self._content(interaction)
        self.assertNotIn("Alien", content)
        self.assertNotIn("Matches:", content)

    async def test_ambiguous_collections_show_a_picker_before_the_filter_menu(self) -> None:
        self.bot.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1)

        interaction = FakeInteraction(user=self._member(1), channel_id=999999)
        await handle_browse(interaction, self.bot)

        self.assertIn("Which collection", interaction.response.sent_message)

    async def test_choosing_from_the_ambiguous_picker_lands_on_the_filter_menu(self) -> None:
        second = self.bot.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self._add("Alien")
        self._add("Breaking Bad", database_id=second.database_id)

        interaction = FakeInteraction(user=self._member(1), channel_id=999999)
        await handle_browse(interaction, self.bot)
        picker_view = interaction.response.sent_view
        select = picker_view.children[0]
        select._values = [str(self.database.database_id)]
        select_interaction = FakeInteraction(user=self._member(1))
        await select.callback(interaction=select_interaction)

        # This is the first response to select_interaction -- a fresh
        # send_message, not an edit (see handle_browse's on_resolved).
        self.assertIsInstance(self._screen_view(select_interaction), FilterMenuView)

    async def test_view_results_opens_the_paginated_results_screen(self) -> None:
        self._add("Alien")

        menu_interaction = await self._browse()
        results_interaction = await self._view_results(menu_interaction)

        content = self._content(results_interaction)
        self.assertIn("Alien", content)
        self.assertIn("Matches: 1", content)


class PermissionTests(BrowseCommandTestCase):
    async def test_non_watch_party_member_is_rejected(self) -> None:
        interaction = FakeInteraction(user=self._member(1, watch_party=False))

        await handle_browse(interaction, self.bot)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(interaction.response.sent_view)

    async def test_watch_party_member_can_browse(self) -> None:
        self._add("Alien")

        interaction = await self._browse(crew=False)

        self.assertIsInstance(self._screen_view(interaction), FilterMenuView)

    async def test_non_crew_does_not_see_crew_buttons(self) -> None:
        self._add("Alien")

        interaction = await self._browse_to_results(crew=False)

        custom_ids = {c.custom_id for c in self._screen_view(interaction).children}
        self.assertNotIn("wpm_browse_random_pick", custom_ids)
        self.assertNotIn("wpm_browse_start_vote", custom_ids)
        self.assertNotIn("wpm_browse_post_publicly", custom_ids)

    async def test_crew_sees_all_crew_buttons(self) -> None:
        self._add("Alien")

        interaction = await self._browse_to_results(crew=True)

        custom_ids = {c.custom_id for c in self._screen_view(interaction).children}
        self.assertIn("wpm_browse_random_pick", custom_ids)
        self.assertIn("wpm_browse_start_vote", custom_ids)
        self.assertIn("wpm_browse_post_publicly", custom_ids)

    async def test_everyone_sees_change_filters_and_change_collection(self) -> None:
        self._add("Alien")

        interaction = await self._browse_to_results(crew=False)

        custom_ids = {c.custom_id for c in self._screen_view(interaction).children}
        self.assertIn("wpm_browse_change_filters", custom_ids)
        self.assertIn("wpm_browse_change_collection", custom_ids)


class CollectionSelectionTests(BrowseCommandTestCase):
    async def test_single_collection_resolves_automatically(self) -> None:
        self._add("Alien")

        interaction = await self._browse()

        self.assertIn("Movies", self._content(interaction))

    async def test_change_collection_lists_every_active_collection(self) -> None:
        second = self.bot.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self._add("Alien")
        self._add("Breaking Bad", database_id=second.database_id)

        interaction = await self._browse_to_results()
        change_interaction = await self._click(self._screen_view(interaction), "wpm_browse_change_collection")

        picker_view = change_interaction.response.edited_view
        select = picker_view.children[0]
        self.assertEqual(len(select.options), 2)

    async def test_change_collection_returns_to_the_filter_menu_not_results(self) -> None:
        second = self.bot.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self._add("Alien")
        self._add("Breaking Bad", database_id=second.database_id)

        interaction = await self._browse_to_results()
        change_interaction = await self._click(self._screen_view(interaction), "wpm_browse_change_collection")
        picker_view = change_interaction.response.edited_view
        select = picker_view.children[0]
        select._values = [str(second.database_id)]
        select_interaction = FakeInteraction(user=self._member(1))
        await select.callback(interaction=select_interaction)

        self.assertIsInstance(select_interaction.response.edited_view, FilterMenuView)
        self.assertIn("TV Shows", select_interaction.response.edited_content)
        self.assertNotIn("Breaking Bad", select_interaction.response.edited_content)

    async def test_change_collection_rebuilds_dynamic_filter_options(self) -> None:
        second = self.bot.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self._add("Alien", genres=("Horror",))
        self._add("Breaking Bad", database_id=second.database_id, genres=("Crime",))

        interaction = await self._browse_to_results()
        change_interaction = await self._click(self._screen_view(interaction), "wpm_browse_change_collection")
        picker_view = change_interaction.response.edited_view
        select = picker_view.children[0]
        select._values = [str(second.database_id)]
        select_interaction = FakeInteraction(user=self._member(1))
        await select.callback(interaction=select_interaction)

        new_menu_view = select_interaction.response.edited_view
        new_menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_interaction = FakeInteraction(user=self._member(1))
        await new_menu_view.children[0].callback(interaction=genre_edit_interaction)
        genre_options = {option.value for option in genre_edit_interaction.response.edited_view.children[0].options}

        self.assertEqual(genre_options, {"Crime"})

    async def test_change_collection_clears_only_invalid_filters(self) -> None:
        second = self.bot.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self._add("Alien", genres=("Horror",), imdb_rating="8.5")
        self._add("Breaking Bad", database_id=second.database_id, genres=("Crime",), imdb_rating="9.5")

        menu_interaction = await self._browse()
        genre_set_interaction = await self._set_genre_filter(menu_interaction, "Horror")
        menu_view = genre_set_interaction.response.edited_view
        menu_view.children[0]._values = [FILTER_CATEGORY_IMDB_RATING]
        imdb_edit_interaction = FakeInteraction(user=self._member(1))
        await menu_view.children[0].callback(interaction=imdb_edit_interaction)
        set_button = next(
            c for c in imdb_edit_interaction.response.edited_view.children if c.custom_id == "wpm_filter_menu_imdb_rating_set"
        )
        open_interaction = FakeInteraction(user=self._member(1))
        await set_button.callback(interaction=open_interaction)
        modal = open_interaction.response.sent_modal
        modal.minimum_input._value = "5.0"
        modal.maximum_input._value = None
        submit_interaction = FakeInteraction(user=self._member(1))
        await modal.on_submit(interaction=submit_interaction)

        results_interaction = await self._view_results(submit_interaction)
        change_interaction = await self._click(self._screen_view(results_interaction), "wpm_browse_change_collection")
        select = change_interaction.response.edited_view.children[0]
        select._values = [str(second.database_id)]
        select_interaction = FakeInteraction(user=self._member(1))
        await select.callback(interaction=select_interaction)

        content = select_interaction.response.edited_content
        # Genre ("Horror") no longer matches anything in TV Shows -- cleared.
        self.assertIn("Any Genre", content)
        # The IMDb Rating range isn't tied to a specific collection's
        # enumerated values -- preserved across the collection change.
        self.assertIn("5.0+", content)


class FilterReuseTests(BrowseCommandTestCase):
    async def test_change_filters_opens_the_shared_filter_menu(self) -> None:
        self._add("Alien", genres=("Horror",))

        interaction = await self._browse_to_results()
        filters_interaction = await self._click(self._screen_view(interaction), "wpm_browse_change_filters")

        self.assertIsInstance(filters_interaction.response.edited_view, FilterMenuView)

    async def test_current_filters_summary_is_shown_on_the_filter_menu(self) -> None:
        self._add("Alien")

        interaction = await self._browse()

        self.assertIn("Current Filters", self._content(interaction))
        self.assertIn("Any Genre", self._content(interaction))

    async def test_setting_a_genre_filter_narrows_results_once_view_results_is_clicked(self) -> None:
        self._add("Alien", genres=("Horror",))
        self._add("Notting Hill", genres=("Romance",))

        menu_interaction = await self._browse()
        genre_set_interaction = await self._set_genre_filter(menu_interaction, "Horror")
        results_interaction = await self._view_results(genre_set_interaction)

        content = self._content(results_interaction)
        self.assertIn("Alien", content)
        self.assertNotIn("Notting Hill", content)
        self.assertIn("Matches: 1", content)

    async def test_change_filters_from_results_preserves_the_current_filter(self) -> None:
        self._add("Alien", genres=("Horror",))
        self._add("Notting Hill", genres=("Romance",))

        menu_interaction = await self._browse()
        genre_set_interaction = await self._set_genre_filter(menu_interaction, "Horror")
        results_interaction = await self._view_results(genre_set_interaction)

        back_to_menu_interaction = await self._click(self._screen_view(results_interaction), "wpm_browse_change_filters")

        self.assertIsInstance(back_to_menu_interaction.response.edited_view, FilterMenuView)
        self.assertIn("Genre .......... Horror", back_to_menu_interaction.response.edited_content)

    async def test_change_filters_preserves_the_selected_collection(self) -> None:
        second = self.bot.suggestion_service.create_database("TV Shows", guild_id=GUILD_ID, channel_id=CHANNEL_ID + 1).database
        self._add("Breaking Bad", database_id=second.database_id)
        self._add("Alien")

        interaction = await self._browse_to_results()
        filters_interaction = await self._click(self._screen_view(interaction), "wpm_browse_change_filters")

        self.assertIn("Movies", filters_interaction.response.edited_content)


class PaginationTests(BrowseCommandTestCase):
    async def test_large_collection_paginates(self) -> None:
        for index in range(60):
            self._add(f"Movie {index:03d}")

        interaction = await self._browse_to_results()

        view = self._screen_view(interaction)
        self.assertIsInstance(view, PaginatedListView)
        self.assertGreater(view.page_count, 1)

    async def test_next_and_previous_move_between_pages(self) -> None:
        for index in range(60):
            self._add(f"Movie {index:03d}")

        interaction = await self._browse_to_results()
        view = self._screen_view(interaction)

        # PaginationButton has no fixed custom_id -- select by label instead.
        next_button = next(c for c in view.children if getattr(c, "label", None) == "Next")
        next_interaction = FakeInteraction(user=self._member(1))
        await next_button.callback(interaction=next_interaction)

        self.assertIn("Page 2", next_interaction.response.edited_content)

        previous_button = next(c for c in view.children if getattr(c, "label", None) == "Previous")
        previous_interaction = FakeInteraction(user=self._member(1))
        await previous_button.callback(interaction=previous_interaction)

        self.assertIn("Page 1", previous_interaction.response.edited_content)

    async def test_small_collection_has_no_pagination_buttons(self) -> None:
        self._add("Alien")

        interaction = await self._browse_to_results()

        view = self._screen_view(interaction)
        labels = {getattr(c, "label", None) for c in view.children}
        self.assertNotIn("Previous", labels)
        self.assertNotIn("Next", labels)

    async def test_pagination_never_rebuilds_the_filter_menu(self) -> None:
        for index in range(60):
            self._add(f"Movie {index:03d}")

        interaction = await self._browse_to_results()
        view = self._screen_view(interaction)
        next_button = next(c for c in view.children if getattr(c, "label", None) == "Next")
        next_interaction = FakeInteraction(user=self._member(1))
        await next_button.callback(interaction=next_interaction)

        self.assertIsInstance(next_interaction.response.edited_view, PaginatedListView)


class EmptyResultsTests(BrowseCommandTestCase):
    async def test_empty_collection_explains_and_offers_change_filters_collection_and_back(self) -> None:
        interaction = await self._browse_to_results()

        content = self._content(interaction)
        self.assertIn("No suggestions match the current filters", content)
        custom_ids = {c.custom_id for c in self._screen_view(interaction).children}
        self.assertEqual(
            custom_ids, {"wpm_browse_change_filters", "wpm_browse_change_collection", "wpm_browse_empty_back"}
        )

    async def test_filters_narrowing_to_zero_matches_shows_the_same_message(self) -> None:
        self._add("Alien", imdb_rating="8.5")

        menu_interaction = await self._browse()
        menu_view = self._screen_view(menu_interaction)
        menu_view.children[0]._values = [FILTER_CATEGORY_IMDB_RATING]
        imdb_edit_interaction = FakeInteraction(user=self._member(1))
        await menu_view.children[0].callback(interaction=imdb_edit_interaction)
        imdb_edit_view = imdb_edit_interaction.response.edited_view
        set_button = next(c for c in imdb_edit_view.children if c.custom_id == "wpm_filter_menu_imdb_rating_set")
        open_interaction = FakeInteraction(user=self._member(1))
        await set_button.callback(interaction=open_interaction)
        modal = open_interaction.response.sent_modal
        modal.minimum_input._value = "9.9"
        modal.maximum_input._value = None
        submit_interaction = FakeInteraction(user=self._member(1))
        await modal.on_submit(interaction=submit_interaction)

        results_interaction = await self._view_results(submit_interaction)

        self.assertIn("No suggestions match the current filters", results_interaction.response.edited_content)
        custom_ids = {c.custom_id for c in results_interaction.response.edited_view.children}
        self.assertEqual(
            custom_ids, {"wpm_browse_change_filters", "wpm_browse_change_collection", "wpm_browse_empty_back"}
        )

    async def test_back_button_returns_to_the_filter_menu(self) -> None:
        interaction = await self._browse_to_results()

        back_interaction = await self._click(self._screen_view(interaction), "wpm_browse_empty_back")

        self.assertIsInstance(back_interaction.response.edited_view, FilterMenuView)

    async def test_empty_results_never_shows_crew_actions(self) -> None:
        interaction = await self._browse_to_results(crew=True)

        custom_ids = {c.custom_id for c in self._screen_view(interaction).children}
        self.assertNotIn("wpm_browse_random_pick", custom_ids)
        self.assertNotIn("wpm_browse_start_vote", custom_ids)
        self.assertNotIn("wpm_browse_post_publicly", custom_ids)


class RandomPickTests(BrowseCommandTestCase):
    async def test_random_pick_chooses_from_the_filtered_pool_only(self) -> None:
        self._add("Alien", genres=("Horror",))
        self._add("Notting Hill", genres=("Romance",))
        menu_interaction = await self._browse()
        genre_set_interaction = await self._set_genre_filter(menu_interaction, "Horror")
        results_interaction = await self._view_results(genre_set_interaction)

        pick_interaction = await self._click(self._screen_view(results_interaction), "wpm_browse_random_pick")

        self.assertTrue(pick_interaction.response.deferred)
        self.assertIsNotNone(pick_interaction.followup.sent_embed)
        self.assertEqual(pick_interaction.followup.sent_embed.title, "Alien")
        self.assertFalse(pick_interaction.followup.sent_ephemeral)

    async def test_random_pick_does_not_edit_the_ephemeral_browse_screen(self) -> None:
        self._add("Alien")

        interaction = await self._browse_to_results()
        pick_interaction = await self._click(self._screen_view(interaction), "wpm_browse_random_pick")

        # response.edit_message() was never called for the click
        # interaction -- only response.defer() + a followup -- so the
        # click interaction's own edited_content stays at its sentinel.
        self.assertIsNone(pick_interaction.response.edited_content)
        self.assertTrue(pick_interaction.response.deferred)

    async def test_random_pick_empty_pool_reports_privately(self) -> None:
        # Random Pick is never even offered once the browse screen itself
        # has zero matches (see EmptyResultsTests) -- this exercises
        # perform_browse_random_pick's own defensive empty-pool branch
        # directly, the same "belt and suspenders" pattern the rest of
        # this codebase uses for filter-state guards.
        from watch_party_manager.bot import perform_browse_random_pick

        interaction = FakeInteraction(user=self._member(1))
        await perform_browse_random_pick(interaction, self.bot, self.database, "Movies", [], {})

        self.assertIn("No suggestions match", interaction.followup.sent_content)
        self.assertTrue(interaction.followup.sent_ephemeral)

    async def test_pick_again_reuses_the_same_filtered_pool(self) -> None:
        self._add("Alien")
        interaction = await self._browse_to_results()
        pick_interaction = await self._click(self._screen_view(interaction), "wpm_browse_random_pick")
        result_view = pick_interaction.followup.sent_view
        self.assertIsInstance(result_view, RandomWatchResultView)

        pick_again_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_pick_again")
        again_interaction = FakeInteraction(user=self._member(1))
        await pick_again_button.callback(interaction=again_interaction)

        self.assertIsNotNone(again_interaction.followup.sent_embed)


class StartVoteTests(BrowseCommandTestCase):
    async def test_non_crew_cannot_see_start_vote(self) -> None:
        self._add("Alien")
        interaction = await self._browse_to_results(crew=False)
        custom_ids = {c.custom_id for c in self._screen_view(interaction).children}
        self.assertNotIn("wpm_browse_start_vote", custom_ids)

    async def test_start_vote_opens_customize_screen_for_the_current_collection(self) -> None:
        self._add("Alien")
        self._add("Predator")
        interaction = await self._browse_to_results()

        vote_interaction = await self._click(self._screen_view(interaction), "wpm_browse_start_vote")

        # The collection is already resolved (no "which collection?"
        # ambiguity picker) -- CustomizeVoteOverridesView only offers
        # Edit Filters once a database_id is known (see
        # show_customize_vote_overrides).
        overrides_view = vote_interaction.response.sent_view
        self.assertIsInstance(overrides_view, CustomizeVoteOverridesView)
        custom_ids = {c.custom_id for c in overrides_view.children}
        self.assertIn("wpm_start_vote_customize_edit_filters", custom_ids)

    async def test_start_vote_carries_over_the_current_filter(self) -> None:
        self._add("Alien", genres=("Horror",))
        self._add("Notting Hill", genres=("Romance",))
        menu_interaction = await self._browse()
        genre_set_interaction = await self._set_genre_filter(menu_interaction, "Horror")
        results_interaction = await self._view_results(genre_set_interaction)

        vote_interaction = await self._click(self._screen_view(results_interaction), "wpm_browse_start_vote")

        self.assertIn("Genre .......... Horror", vote_interaction.response.sent_message)


class PostPubliclyTests(BrowseCommandTestCase):
    async def test_non_crew_cannot_see_post_publicly(self) -> None:
        self._add("Alien")
        interaction = await self._browse_to_results(crew=False)
        custom_ids = {c.custom_id for c in self._screen_view(interaction).children}
        self.assertNotIn("wpm_browse_post_publicly", custom_ids)

    async def test_post_publicly_sends_a_new_public_message_with_a_dedicated_view(self) -> None:
        self._add("Alien")
        interaction = await self._browse_to_results()

        post_interaction = await self._click(self._screen_view(interaction), "wpm_browse_post_publicly")

        self.assertTrue(post_interaction.response.deferred)
        self.assertFalse(post_interaction.followup.sent_ephemeral)
        self.assertIn("Alien", post_interaction.followup.sent_content)
        self.assertIn("Matches: 1", post_interaction.followup.sent_content)
        self.assertIn("Movies", post_interaction.followup.sent_content)

    async def test_post_publicly_single_page_has_no_pagination_view(self) -> None:
        self._add("Alien")
        interaction = await self._browse_to_results()

        post_interaction = await self._click(self._screen_view(interaction), "wpm_browse_post_publicly")

        self.assertIsNone(post_interaction.followup.sent_view)

    async def test_post_publicly_multi_page_uses_the_dedicated_public_view(self) -> None:
        for index in range(60):
            self._add(f"Movie {index:03d}")
        interaction = await self._browse_to_results()

        post_interaction = await self._click(self._screen_view(interaction), "wpm_browse_post_publicly")

        self.assertIsInstance(post_interaction.followup.sent_view, BrowsePublicResultsView)

    async def test_only_the_posting_crew_member_can_page_the_public_view(self) -> None:
        for index in range(60):
            self._add(f"Movie {index:03d}")
        interaction = await self._browse_to_results()
        post_interaction = await self._click(self._screen_view(interaction), "wpm_browse_post_publicly")
        public_view = post_interaction.followup.sent_view

        other_member_interaction = FakeInteraction(user=self._member(2))
        allowed = await public_view.interaction_check(other_member_interaction)

        self.assertFalse(allowed)
        self.assertTrue(other_member_interaction.response.sent_message)

    async def test_the_posting_crew_member_can_page_the_public_view(self) -> None:
        for index in range(60):
            self._add(f"Movie {index:03d}")
        interaction = await self._browse_to_results()
        post_interaction = await self._click(self._screen_view(interaction), "wpm_browse_post_publicly")
        public_view = post_interaction.followup.sent_view

        same_member_interaction = FakeInteraction(user=self._member(1))
        allowed = await public_view.interaction_check(same_member_interaction)

        self.assertTrue(allowed)


class LegacyCollectionTests(BrowseCommandTestCase):
    async def test_suggestions_missing_all_optional_metadata_render_without_error(self) -> None:
        self._add("Untitled Legacy Suggestion")

        interaction = await self._browse_to_results()

        self.assertIn("Untitled Legacy Suggestion", self._content(interaction))


if __name__ == "__main__":
    unittest.main()
