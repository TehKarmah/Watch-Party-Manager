"""Integration tests for /random_watch: a discovery-only command that
picks one uniformly-random eligible watch item from a collection.

Exercises the real bot.py wiring (handle_random_watch,
send_random_watch_session, show_random_watch_collection_picker) against
real SuggestionService/CollectionEligibilityService/VoteService/
PermissionService instances, with lightweight fakes only for Discord
objects -- mirroring the project's established test convention (see
test_start_vote_flow.py, test_database_manage_command.py).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.bot import (
    build_random_watch_empty_pool_message,
    build_random_watch_result_embed,
    build_random_watch_result_header,
    handle_random_watch,
    show_random_watch_collection_picker,
)
from watch_party_manager.domain.watch_item import WatchItemStatus
from watch_party_manager.persistence.suggestion_database_configuration_repository import (
    SuggestionDatabaseConfigurationRepository,
)
from watch_party_manager.persistence.suggestion_database_repository import JsonSuggestionDatabaseRepository
from watch_party_manager.persistence.suggestion_repository import JsonSuggestionRepository
from watch_party_manager.persistence.vote_repository import JsonVoteRepository
from watch_party_manager.filter_menu_view import (
    ANY_MPAA_RATING_VALUE,
    FILTER_CATEGORY_ACTOR,
    FILTER_CATEGORY_GENRE,
    FILTER_CATEGORY_IMDB_RATING,
    FILTER_CATEGORY_MEMBER,
    FILTER_CATEGORY_MPAA_RATING,
    FilterMenuView,
)
from watch_party_manager.random_watch_view import (
    RandomWatchInitialView,
    RandomWatchResultView,
)
from watch_party_manager.services.collection_eligibility_service import CollectionEligibilityService
from watch_party_manager.services.permission_service import PermissionService
from watch_party_manager.services.suggestion_service import SuggestionService
from watch_party_manager.services.vote_service import VoteService

GUILD_ID = 100
WASH_CREW_ROLE_ID = 900
WATCH_PARTY_ROLE_ID = 800
CHANNEL_ID = 200


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, user_id: int, roles=(), display_name: str | None = None) -> None:
        self.id = user_id
        self.roles = list(roles)
        self.display_name = display_name if display_name is not None else f"User {user_id}"


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_embed = None
        self.sent_ephemeral = None
        self.sent_view = None
        self.sent_modal = None
        self.edited_content = None
        self.edited_embed = "not-edited"
        self.edited_view = "not-edited"
        self.edit_calls = []

    async def send_message(self, content=None, ephemeral=False, view=None, embed=None) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral
        self.sent_view = view
        self.sent_embed = embed

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal

    async def edit_message(self, content=None, view=None, embed=None) -> None:
        self.edited_content = content
        self.edited_view = view
        self.edited_embed = embed
        self.edit_calls.append((content, embed, view))


class FakeFollowup:
    """/random_watch's public result is posted via interaction.followup.send
    once the private setup screen has been closed out with a hand-off
    edit (see bot.py's perform_pick). Mirroring the sent content/embed/
    view back onto the same FakeResponse's edited_* fields keeps every
    pre-existing "final result" assertion working unchanged (they read
    the end state, not the intermediate hand-off edit) while still
    letting dedicated tests inspect the followup's own fields --
    including `sent_ephemeral`, which must be falsy -- to verify the
    result really was posted publicly and separately from the hand-off.
    """

    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.sent_content = None
        self.sent_embed = None
        self.sent_view = None
        self.sent_ephemeral = None

    async def send(self, content=None, embed=None, view=None, ephemeral=False) -> None:
        self.sent_content = content
        self.sent_embed = embed
        self.sent_view = view
        self.sent_ephemeral = ephemeral
        self._response.edited_content = content
        self._response.edited_embed = embed
        self._response.edited_view = view


class FakeInteraction:
    def __init__(self, *, user=None, guild_id=GUILD_ID, channel_id=CHANNEL_ID, guild=None) -> None:
        self.user = user if user is not None else FakeMember(1, roles=[FakeRole(WATCH_PARTY_ROLE_ID)])
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.guild = guild
        self.response = FakeResponse()
        self.followup = FakeFollowup(self.response)


class FakeMembershipService:
    """Minimal MembershipService stand-in -- only get_role_config() is
    called through bot.membership_service; is_current_member() is the
    real MembershipService staticmethod.
    """

    def __init__(self, role_id: int | None) -> None:
        self._role_id = role_id

    def get_role_config(self, guild_id):
        from types import SimpleNamespace

        return SimpleNamespace(role_id=self._role_id)


class FakeBot:
    def __init__(self, root: Path, *, wash_crew_role_id=WASH_CREW_ROLE_ID, watch_party_role_id=WATCH_PARTY_ROLE_ID) -> None:
        self.suggestion_service = SuggestionService(
            repository=JsonSuggestionRepository(root / "suggestions.json"),
            database_repository=JsonSuggestionDatabaseRepository(root / "suggestion_databases.json"),
        )
        self.vote_service = VoteService(self.suggestion_service, repository=JsonVoteRepository(root / "voting.json"))
        self.suggestion_database_configuration_repository = SuggestionDatabaseConfigurationRepository(
            root / "suggestion_database_configurations.json"
        )
        self.collection_eligibility_service = CollectionEligibilityService(self.suggestion_service, self.vote_service)
        self.permission_service = PermissionService(
            watch_party_member_role_id=watch_party_role_id, wash_crew_role_id=wash_crew_role_id
        )
        self.membership_service = FakeMembershipService(watch_party_role_id)
        self.wash_crew_role_id = wash_crew_role_id


class RandomWatchCommandTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self.root = Path(self._temp_dir.name)
        self.bot = FakeBot(self.root)

    def _create_database(self, name: str, channel_id: int = CHANNEL_ID) -> int:
        result = self.bot.suggestion_service.create_database(name, guild_id=GUILD_ID, channel_id=channel_id)
        self.assertTrue(result.success, result.message)
        return result.database.database_id

    def _add(self, title: str, database_id: int, **kwargs):
        result = self.bot.suggestion_service.suggest(title, database_id=database_id, guild_id=GUILD_ID, **kwargs)
        self.assertTrue(result.success, result.message)
        return result.watch_item

    def _member(self, user_id: int, *, watch_party: bool = True) -> FakeMember:
        roles = [FakeRole(WATCH_PARTY_ROLE_ID)] if watch_party else []
        return FakeMember(user_id, roles=roles)

    # --- Shared FilterMenuView navigation helpers (Genre/IMDb Rating/
    # MPAA Rating/Actor/Member all share this one menu architecture --
    # see bot.py's create_filter_menu_session) ------------------------

    async def _reach_filter_menu(self, database_id: int):
        """/random watch -> Add Filters -> the FilterMenuView."""
        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        initial_view = interaction.response.sent_view
        add_filters_button = next(c for c in initial_view.children if c.custom_id == "wpm_random_watch_add_filters")
        filters_interaction = FakeInteraction(user=self._member(1))
        await add_filters_button.callback(interaction=filters_interaction)
        return filters_interaction.response.edited_view

    async def _open_category_edit(self, menu_view):
        """Click the category select on a FilterMenuView, returning the
        resulting edit screen for whichever category was selected --
        menu_view.children[0]._values must already be set by the caller.
        """
        category_select = menu_view.children[0]
        interaction = FakeInteraction(user=self._member(1))
        await category_select.callback(interaction=interaction)
        return interaction.response.edited_view

    async def _set_member_filter(self, menu_view, fake_user):
        """Navigate Filters -> Member -> select fake_user -> Back to
        Filters. Returns (menu_view, status_line_content) -- the status
        line is whatever validate_member_filter_selection produced,
        shown on the Member edit screen itself before returning.
        """
        menu_view.children[0]._values = [FILTER_CATEGORY_MEMBER]
        member_edit_view = await self._open_category_edit(menu_view)
        member_select = member_edit_view.children[0]
        member_select._values = [fake_user]
        member_interaction = FakeInteraction(user=self._member(1))
        await member_select.callback(interaction=member_interaction)
        status_content = member_interaction.response.edited_content
        updated_member_edit_view = member_interaction.response.edited_view
        back_button = updated_member_edit_view.children[-1]
        back_interaction = FakeInteraction(user=self._member(1))
        await back_button.callback(interaction=back_interaction)
        return back_interaction.response.edited_view, status_content

    async def _clear_member_filter(self, menu_view):
        menu_view.children[0]._values = [FILTER_CATEGORY_MEMBER]
        member_edit_view = await self._open_category_edit(menu_view)
        member_select = member_edit_view.children[0]
        member_select._values = []
        member_interaction = FakeInteraction(user=self._member(1))
        await member_select.callback(interaction=member_interaction)
        updated_member_edit_view = member_interaction.response.edited_view
        back_button = updated_member_edit_view.children[-1]
        back_interaction = FakeInteraction(user=self._member(1))
        await back_button.callback(interaction=back_interaction)
        return back_interaction.response.edited_view

    async def _set_genre_filter(self, menu_view, genre: str):
        """Navigate Filters -> Genre -> select genre. Genre selection
        returns directly to the FilterMenuView (no Back step needed --
        unlike Member, which shows its own inline validation feedback
        first)."""
        menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_view = await self._open_category_edit(menu_view)
        genre_select = genre_edit_view.children[0]
        genre_select._values = [genre]
        interaction = FakeInteraction(user=self._member(1))
        await genre_select.callback(interaction=interaction)
        return interaction.response.edited_view

    async def _clear_genre_filter(self, menu_view):
        menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_view = await self._open_category_edit(menu_view)
        genre_select = genre_edit_view.children[0]
        genre_select._values = []
        interaction = FakeInteraction(user=self._member(1))
        await genre_select.callback(interaction=interaction)
        return interaction.response.edited_view

    @staticmethod
    def _pick_button(menu_view):
        return next(c for c in menu_view.children if c.custom_id == "wpm_random_watch_pick_filtered")

    async def _set_imdb_rating_filter(self, menu_view, *, minimum=None, maximum=None):
        """Navigate Filters -> IMDb Rating -> Set Minimum/Maximum... ->
        submit modal. Returns the submit interaction -- success lands
        back on the refreshed menu (edited_view), a validation error
        stays on the IMDb Rating edit screen with an inline warning."""
        menu_view.children[0]._values = [FILTER_CATEGORY_IMDB_RATING]
        imdb_edit_view = await self._open_category_edit(menu_view)
        set_button = next(c for c in imdb_edit_view.children if c.custom_id == "wpm_filter_menu_imdb_rating_set")
        open_interaction = FakeInteraction(user=self._member(1))
        await set_button.callback(interaction=open_interaction)
        modal = open_interaction.response.sent_modal
        modal.minimum_input._value = minimum
        modal.maximum_input._value = maximum
        submit_interaction = FakeInteraction(user=self._member(1))
        await modal.on_submit(interaction=submit_interaction)
        return submit_interaction

    async def _clear_imdb_rating_filter(self, menu_view):
        menu_view.children[0]._values = [FILTER_CATEGORY_IMDB_RATING]
        imdb_edit_view = await self._open_category_edit(menu_view)
        any_button = next(c for c in imdb_edit_view.children if c.custom_id == "wpm_filter_menu_imdb_rating_any")
        interaction = FakeInteraction(user=self._member(1))
        await any_button.callback(interaction=interaction)
        return interaction.response.edited_view

    async def _set_mpaa_rating_filter(self, menu_view, rating: str):
        menu_view.children[0]._values = [FILTER_CATEGORY_MPAA_RATING]
        mpaa_edit_view = await self._open_category_edit(menu_view)
        mpaa_select = mpaa_edit_view.children[0]
        mpaa_select._values = [rating]
        interaction = FakeInteraction(user=self._member(1))
        await mpaa_select.callback(interaction=interaction)
        return interaction.response.edited_view

    async def _clear_mpaa_rating_filter(self, menu_view):
        menu_view.children[0]._values = [FILTER_CATEGORY_MPAA_RATING]
        mpaa_edit_view = await self._open_category_edit(menu_view)
        mpaa_select = mpaa_edit_view.children[0]
        mpaa_select._values = [ANY_MPAA_RATING_VALUE]
        interaction = FakeInteraction(user=self._member(1))
        await mpaa_select.callback(interaction=interaction)
        return interaction.response.edited_view

    async def _search_actor_filter(self, menu_view, query):
        """Navigate Filters -> Actor -> Search for an Actor... -> submit
        modal. Returns the submit interaction; caller inspects
        edited_view/edited_content to see whether it landed back on the
        refreshed menu (single match), a disambiguation picker (multiple
        matches), or stayed on the Actor edit screen with a warning (no
        matches)."""
        menu_view.children[0]._values = [FILTER_CATEGORY_ACTOR]
        actor_edit_view = await self._open_category_edit(menu_view)
        search_button = next(c for c in actor_edit_view.children if c.custom_id == "wpm_filter_menu_actor_search")
        open_interaction = FakeInteraction(user=self._member(1))
        await search_button.callback(interaction=open_interaction)
        modal = open_interaction.response.sent_modal
        modal.query_input._value = query
        submit_interaction = FakeInteraction(user=self._member(1))
        await modal.on_submit(interaction=submit_interaction)
        return submit_interaction

    async def _clear_actor_filter(self, menu_view):
        menu_view.children[0]._values = [FILTER_CATEGORY_ACTOR]
        actor_edit_view = await self._open_category_edit(menu_view)
        any_button = next(c for c in actor_edit_view.children if c.custom_id == "wpm_filter_menu_actor_any")
        interaction = FakeInteraction(user=self._member(1))
        await any_button.callback(interaction=interaction)
        return interaction.response.edited_view


# --- Section 1: Command access ------------------------------------------------------------------


class AccessTests(RandomWatchCommandTestCase):
    async def test_allowed_watch_party_member_proceeds(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        interaction = FakeInteraction(user=self._member(1))

        await handle_random_watch(interaction, self.bot)

        self.assertIsNotNone(interaction.response.sent_view)
        self.assertNotIn("Watch Party member", interaction.response.sent_message or "")

    async def test_non_member_is_blocked(self) -> None:
        interaction = FakeInteraction(user=self._member(1, watch_party=False))

        await handle_random_watch(interaction, self.bot)

        self.assertIn("Watch Party member", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIsNone(interaction.response.sent_view)

    async def test_wash_crew_is_allowed_too(self) -> None:
        # WASH Crew always inherits Watch Party member capability.
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        crew_member = FakeMember(1, roles=[FakeRole(WASH_CREW_ROLE_ID)])
        interaction = FakeInteraction(user=crew_member)

        await handle_random_watch(interaction, self.bot)

        self.assertIsNotNone(interaction.response.sent_view)

    async def test_rejects_use_outside_a_guild(self) -> None:
        interaction = FakeInteraction(user=self._member(1), guild_id=None)

        await handle_random_watch(interaction, self.bot)

        self.assertIn("server", interaction.response.sent_message)

    async def test_fails_closed_when_no_roles_are_configured(self) -> None:
        self.bot.permission_service = PermissionService(watch_party_member_role_id=None, wash_crew_role_id=None)
        interaction = FakeInteraction(user=self._member(1))

        await handle_random_watch(interaction, self.bot)

        self.assertTrue(interaction.response.sent_ephemeral)
        self.assertIn("before using this command", interaction.response.sent_message)


# --- Section 2: Collection selection ------------------------------------------------------------


class CollectionSelectionTests(RandomWatchCommandTestCase):
    async def test_no_collections_shows_a_clear_message(self) -> None:
        interaction = FakeInteraction(user=self._member(1))

        await handle_random_watch(interaction, self.bot)

        self.assertIn("create a collection", interaction.response.sent_message)
        self.assertIsNone(interaction.response.sent_view)

    async def test_one_collection_is_used_automatically(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        interaction = FakeInteraction(user=self._member(1))

        await handle_random_watch(interaction, self.bot)

        self.assertIsInstance(interaction.response.sent_view, RandomWatchInitialView)
        self.assertIn("Movies", interaction.response.sent_message)

    async def test_multiple_collections_require_a_choice(self) -> None:
        self._create_database("Movies", channel_id=201)
        self._create_database("TV Shows", channel_id=202)
        interaction = FakeInteraction(user=self._member(1), channel_id=999)

        await handle_random_watch(interaction, self.bot)

        self.assertIn("collection", interaction.response.sent_message.lower())
        self.assertNotIsInstance(interaction.response.sent_view, RandomWatchInitialView)

    async def test_change_collection_returns_to_the_picker(self) -> None:
        first_id = self._create_database("Movies", channel_id=201)
        self._create_database("TV Shows", channel_id=202)
        self._add("Alien", first_id)

        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        # Only one matched by channel context (201 != CHANNEL_ID), so both
        # exist and channel 200 matches neither -> ambiguous picker shown
        # directly from the initial command already covers the multi-db
        # case; here we directly exercise the dedicated picker helper
        # Change Collection calls.
        picker_interaction = FakeInteraction(user=self._member(1))
        await show_random_watch_collection_picker(picker_interaction, self.bot, GUILD_ID, edit=False)

        self.assertIn("Choose a collection", picker_interaction.response.sent_message)
        select = picker_interaction.response.sent_view.children[0]
        self.assertLessEqual(len(select.options), 25)

    async def test_change_collection_with_a_single_collection_uses_it_directly(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        interaction = FakeInteraction(user=self._member(1))

        await show_random_watch_collection_picker(interaction, self.bot, GUILD_ID, edit=False)

        self.assertIsInstance(interaction.response.sent_view, RandomWatchInitialView)

    async def test_collection_picker_caps_options_at_twenty_five(self) -> None:
        for index in range(30):
            self._create_database(f"Collection {index}", channel_id=300 + index)
        interaction = FakeInteraction(user=self._member(1))

        await show_random_watch_collection_picker(interaction, self.bot, GUILD_ID, edit=False)

        select = interaction.response.sent_view.children[0]
        self.assertLessEqual(len(select.options), 25)


# --- Section 3/4: True random behavior + eligibility ---------------------------------------------


class RandomSelectionAndEligibilityTests(RandomWatchCommandTestCase):
    async def _reach_initial_screen(self, database_id: int):
        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        return interaction

    async def test_pick_random_item_uses_the_full_eligible_pool_directly(self) -> None:
        database_id = self._create_database("Movies")
        item_a = self._add("Alien", database_id)
        item_b = self._add("The Matrix", database_id)
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        with patch("watch_party_manager.bot.choose_random_watch_item") as mock_choose:
            mock_choose.return_value = item_a
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)

        # The pool passed to the chooser must be exactly the collection's
        # eligible items -- no weighting/strategy wrapper involved.
        called_pool = mock_choose.call_args.args[0]
        self.assertEqual({item.id for item in called_pool}, {item_a.id, item_b.id})

    async def test_available_item_is_eligible(self) -> None:
        database_id = self._create_database("Movies")
        item = self._add("Alien", database_id)
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertIsInstance(pick_interaction.response.edited_view, RandomWatchResultView)
        self.assertEqual(pick_interaction.response.edited_embed.title, "Alien")

    async def test_in_active_vote_is_excluded(self) -> None:
        database_id = self._create_database("Movies")
        nominated_a = self._add("Alien", database_id)
        nominated_b = self._add("Aliens", database_id)
        available = self._add("The Matrix", database_id)
        round_result = self.bot.vote_service.create_round(
            candidate_suggestion_ids=[nominated_a.id, nominated_b.id], database_id=database_id
        )
        self.assertTrue(round_result.success, round_result.message)
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        for _ in range(15):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "The Matrix")

    async def test_pending_crew_review_is_excluded(self) -> None:
        database_id = self._create_database("Movies")
        rejected = self._add("Alien", database_id)
        available = self._add("The Matrix", database_id)
        self.bot.suggestion_service.reject_suggestion(rejected.id, 1)
        self.bot.suggestion_service.reject_suggestion(rejected.id, 2)  # default threshold 2
        self.assertEqual(
            self.bot.suggestion_service.get_suggestion(rejected.id).status, WatchItemStatus.PENDING_CREW_REVIEW
        )
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        for _ in range(15):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "The Matrix")

    async def test_vote_winner_is_excluded(self) -> None:
        database_id = self._create_database("Movies")
        winner = self._add("Alien", database_id)
        available = self._add("The Matrix", database_id)
        self.bot.suggestion_service.record_vote_win(winner.id, date.today())
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        for _ in range(15):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "The Matrix")

    async def test_watched_is_excluded(self) -> None:
        database_id = self._create_database("Movies")
        watched = self._add("Alien", database_id)
        available = self._add("The Matrix", database_id)
        self.bot.suggestion_service.mark_suggestion_watched(watched.id, date.today())
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        for _ in range(15):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "The Matrix")

    async def test_retired_is_excluded(self) -> None:
        database_id = self._create_database("Movies")
        retired = self._add("Alien", database_id)
        available = self._add("The Matrix", database_id)
        self.bot.suggestion_service.reject_suggestion(retired.id, 1)
        self.bot.suggestion_service.reject_suggestion(retired.id, 2)
        self.bot.suggestion_service.retire_pending_review(retired.id)
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        for _ in range(15):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "The Matrix")

    async def test_no_eligible_items_shows_a_clear_message_naming_the_collection(self) -> None:
        database_id = self._create_database("Movies")
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertIn("Movies", pick_interaction.response.edited_content)
        self.assertIn("no eligible watch items", pick_interaction.response.edited_content)

    async def test_no_suggestion_state_changes_from_picking(self) -> None:
        database_id = self._create_database("Movies")
        item = self._add("Alien", database_id)
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        for _ in range(5):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)

        reloaded = self.bot.suggestion_service.get_suggestion(item.id)
        self.assertEqual(reloaded.status, WatchItemStatus.SUGGESTED)
        self.assertEqual(self.bot.vote_service.get_open_round(database_id), None)

    async def test_pick_again_performs_a_new_independent_draw(self) -> None:
        database_id = self._create_database("Movies")
        item_a = self._add("Alien", database_id)
        item_b = self._add("The Matrix", database_id)
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)
        result_view = pick_interaction.response.edited_view
        self.assertIsInstance(result_view, RandomWatchResultView)
        pick_again_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_pick_again")

        with patch("watch_party_manager.bot.choose_random_watch_item") as mock_choose:
            mock_choose.return_value = item_b
            again_interaction = FakeInteraction(user=self._member(1))
            await pick_again_button.callback(interaction=again_interaction)

        self.assertEqual(again_interaction.response.edited_embed.title, "The Matrix")
        # Pick Again drew from the same pool again -- confirms it re-runs
        # the chooser rather than reusing/caching the previous result.
        mock_choose.assert_called_once()

    async def test_immediate_repeat_is_permitted(self) -> None:
        # True random selection must never suppress an immediate repeat --
        # no hidden no-repeat session state.
        database_id = self._create_database("Movies")
        item = self._add("Alien", database_id)
        interaction = await self._reach_initial_screen(database_id)
        pick_button = interaction.response.sent_view.children[0]

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)
        result_view = pick_interaction.response.edited_view
        pick_again_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_pick_again")

        again_interaction = FakeInteraction(user=self._member(1))
        await pick_again_button.callback(interaction=again_interaction)

        # Only one item exists, so the "repeat" is forced here, but the
        # important assertion is that nothing rejected/blocked it.
        self.assertEqual(again_interaction.response.edited_embed.title, "Alien")


# --- Section 6: Member filter ---------------------------------------------------------------------


class MemberFilterTests(RandomWatchCommandTestCase):
    async def test_matches_by_stable_discord_user_id(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        self._add("The Matrix", database_id, original_suggester="222")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        _, status_content = await self._set_member_filter(menu_view, FakeUser())

        self.assertIn("1 eligible suggestion", status_content)

    async def test_legacy_suggestions_without_a_stored_id_never_match(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)  # no original_suggester recorded
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        _, status_content = await self._set_member_filter(menu_view, FakeUser())

        self.assertIn("no eligible suggestions", status_content)

    async def test_rejects_a_non_watch_party_member_selection(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "NotAMember"
            roles = []

        _, status_content = await self._set_member_filter(menu_view, FakeUser())

        self.assertIn(
            "is not the server owner, a WASH Crew member, or a current Watch Party member",
            status_content,
        )

    async def test_only_the_selected_members_suggestions_remain_in_the_pool(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        self._add("The Matrix", database_id, original_suggester="222")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "Alien")

    async def test_clearing_the_member_filter_restores_the_full_pool(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        self._add("The Matrix", database_id, original_suggester="222")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())
        menu_view = await self._clear_member_filter(menu_view)

        category_select = menu_view.children[0]
        current_values = {option.value: option.label for option in category_select.options}
        self.assertIn("Any Member", current_values["member"])
        self.assertIn("Any Genre", current_values["genre"])

    # --- Regression coverage for the live bug (shared with /vote start's
    # Custom Vote Filters -- see services/member_filter_validation.py): an
    # invalid member selection must disable Pick Random Item and never
    # produce a result, not merely reset silently to Any Member.

    async def test_non_member_selection_disables_pick_random_item(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "Midjourney Bot"
            roles = []

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())

        self.assertTrue(self._pick_button(menu_view).disabled)

    async def test_zero_eligible_suggestions_selection_disables_pick_random_item(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="222")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, status_content = await self._set_member_filter(menu_view, FakeUser())

        self.assertTrue(self._pick_button(menu_view).disabled)
        self.assertIn("Movies", status_content)

    async def test_valid_selection_keeps_pick_random_item_enabled(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())

        self.assertFalse(self._pick_button(menu_view).disabled)

    async def test_valid_bot_member_with_eligible_suggestions_is_accepted(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Bot Suggested Movie", database_id, original_suggester="444")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeBotUser:
            id = 444
            display_name = "Midjourney Bot"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, status_content = await self._set_member_filter(menu_view, FakeBotUser())

        self.assertIn("Midjourney Bot has 1 eligible suggestion", status_content)
        self.assertFalse(self._pick_button(menu_view).disabled)

    async def test_server_owner_is_a_valid_member_even_without_any_configured_role(self) -> None:
        from types import SimpleNamespace

        database_id = self._create_database("Movies")
        self._add("Owners Pick", database_id, original_suggester="555")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeOwner:
            id = 555
            display_name = "HeidiTheGreat"
            roles = []

        menu_view.children[0]._values = [FILTER_CATEGORY_MEMBER]
        member_edit_view = await self._open_category_edit(menu_view)
        member_select = member_edit_view.children[0]
        member_select._values = [FakeOwner()]
        member_interaction = FakeInteraction(user=self._member(1), guild=SimpleNamespace(owner_id=555))
        await member_select.callback(interaction=member_interaction)
        status_content = member_interaction.response.edited_content
        back_button = member_interaction.response.edited_view.children[-1]
        back_interaction = FakeInteraction(user=self._member(1))
        await back_button.callback(interaction=back_interaction)
        menu_view = back_interaction.response.edited_view

        self.assertIn("HeidiTheGreat has 1 eligible suggestion", status_content)
        self.assertFalse(self._pick_button(menu_view).disabled)

    async def test_wash_crew_member_is_a_valid_member_even_without_the_watch_party_role(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Crews Pick", database_id, original_suggester="666")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeCrewMember:
            id = 666
            display_name = "Crew"
            roles = [FakeRole(WASH_CREW_ROLE_ID)]

        menu_view, status_content = await self._set_member_filter(menu_view, FakeCrewMember())

        self.assertIn("Crew has 1 eligible suggestion", status_content)
        self.assertFalse(self._pick_button(menu_view).disabled)

    async def test_clicking_pick_random_item_while_invalid_produces_no_result(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "Midjourney Bot"
            roles = []

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())
        pick_button = self._pick_button(menu_view)

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertIsNone(pick_interaction.response.edited_embed)
        self.assertIn(
            "is not the server owner, a WASH Crew member, or a current Watch Party member",
            pick_interaction.response.edited_content,
        )

    async def test_clearing_an_invalid_selection_restores_any_member_and_reenables_pick(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "Midjourney Bot"
            roles = []

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())
        self.assertTrue(self._pick_button(menu_view).disabled)

        menu_view = await self._clear_member_filter(menu_view)
        cleared_pick_button = self._pick_button(menu_view)
        self.assertFalse(cleared_pick_button.disabled)

        category_select = menu_view.children[0]
        member_option = next(o for o in category_select.options if o.value == FILTER_CATEGORY_MEMBER)
        self.assertNotIn("Midjourney Bot", member_option.label)

        pick_interaction = FakeInteraction(user=self._member(1))
        await cleared_pick_button.callback(interaction=pick_interaction)
        self.assertEqual(pick_interaction.response.edited_embed.title, "Alien")




# --- Section 7: Genre filter -----------------------------------------------------------------------


class GenreFilterTests(RandomWatchCommandTestCase):
    async def test_genre_options_are_derived_from_stored_metadata(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, genres=("Horror", "Sci-Fi"))
        menu_view = await self._reach_filter_menu(database_id)
        menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_view = await self._open_category_edit(menu_view)
        genre_select = genre_edit_view.children[0]

        values = {option.value for option in genre_select.options}
        self.assertEqual(values, {"Horror", "Sci-Fi"})

    async def test_matching_is_case_insensitive(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, genres=("Horror",))
        menu_view = await self._reach_filter_menu(database_id)
        updated_view = await self._set_genre_filter(menu_view, "horror")
        pick_button = self._pick_button(updated_view)

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertEqual(pick_interaction.response.edited_embed.title, "Alien")

    async def test_multi_genre_suggestion_matches_any_of_its_genres(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, genres=("Horror", "Sci-Fi"))
        menu_view = await self._reach_filter_menu(database_id)
        updated_view = await self._set_genre_filter(menu_view, "Sci-Fi")
        pick_button = self._pick_button(updated_view)

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertEqual(pick_interaction.response.edited_embed.title, "Alien")

    async def test_suggestions_without_genre_metadata_are_excluded_from_an_active_filter(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, genres=("Horror",))
        self._add("Untagged", database_id)  # no genres
        menu_view = await self._reach_filter_menu(database_id)
        updated_view = await self._set_genre_filter(menu_view, "Horror")
        pick_button = self._pick_button(updated_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "Alien")

    async def test_genre_option_descriptions_show_eligible_counts(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, genres=("Horror",))
        self._add("The Thing", database_id, genres=("Horror",))
        menu_view = await self._reach_filter_menu(database_id)
        menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_view = await self._open_category_edit(menu_view)
        genre_select = genre_edit_view.children[0]

        horror_option = next(o for o in genre_select.options if o.value == "Horror")
        self.assertIn("2 eligible suggestions", horror_option.description)

    async def test_genre_options_are_sorted_deterministically(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, genres=("Horror",))
        self._add("The Thing", database_id, genres=("Horror",))
        self._add("Comedy Movie", database_id, genres=("Comedy",))
        menu_view = await self._reach_filter_menu(database_id)
        menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_view = await self._open_category_edit(menu_view)
        genre_select = genre_edit_view.children[0]

        # Most-represented genre first (Horror: 2), then alphabetical.
        self.assertEqual([option.value for option in genre_select.options], ["Horror", "Comedy"])

    async def test_more_than_twenty_five_genres_are_capped_safely(self) -> None:
        database_id = self._create_database("Movies")
        for index in range(30):
            self._add(f"Movie {index}", database_id, genres=(f"Genre{index:02d}",))
        menu_view = await self._reach_filter_menu(database_id)
        menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_view = await self._open_category_edit(menu_view)
        genre_select = genre_edit_view.children[0]

        self.assertLessEqual(len(genre_select.options), 25)

    async def test_clearing_the_genre_filter_restores_the_full_pool(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, genres=("Horror",))
        self._add("The Matrix", database_id, genres=("Sci-Fi",))
        menu_view = await self._reach_filter_menu(database_id)
        menu_view = await self._set_genre_filter(menu_view, "Horror")
        cleared_view = await self._clear_genre_filter(menu_view)

        category_select = cleared_view.children[0]
        current_values = {option.value: option.label for option in category_select.options}
        self.assertIn("Any Member", current_values["member"])
        self.assertIn("Any Genre", current_values["genre"])


# --- Combined filters -----------------------------------------------------------------------------


class CombinedFilterTests(RandomWatchCommandTestCase):
    async def test_member_and_genre_combine_to_an_intersection(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111", genres=("Horror",))
        self._add("KC Comedy", database_id, original_suggester="111", genres=("Comedy",))
        self._add("Other Horror", database_id, original_suggester="222", genres=("Horror",))
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())
        menu_view = await self._set_genre_filter(menu_view, "Horror")
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "Alien")

    async def test_empty_combined_pool_shows_both_active_filters(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111", genres=("Horror",))
        self._add("KC Comedy", database_id, original_suggester="111", genres=("Comedy",))
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())
        menu_view = await self._set_genre_filter(menu_view, "Sci-Fi")
        pick_button = self._pick_button(menu_view)

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        message = pick_interaction.response.edited_content
        self.assertIn("KC", message)
        self.assertIn("Sci-Fi", message)


# --- Section 6b: IMDb Rating filter --------------------------------------------------------------


class ImdbRatingFilterTests(RandomWatchCommandTestCase):
    async def test_minimum_only_matches_ratings_at_or_above_it(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Low", database_id, imdb_rating="5.5")
        self._add("High", database_id, imdb_rating="8.2")
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="7.0")
        menu_view = submit_interaction.response.edited_view
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "High")

    async def test_maximum_only_matches_ratings_at_or_below_it(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Low", database_id, imdb_rating="5.5")
        self._add("High", database_id, imdb_rating="8.2")
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._set_imdb_rating_filter(menu_view, maximum="5.9")
        menu_view = submit_interaction.response.edited_view
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "Low")

    async def test_minimum_and_maximum_together_are_both_inclusive(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Below", database_id, imdb_rating="5.9")
        self._add("AtMin", database_id, imdb_rating="6.0")
        self._add("AtMax", database_id, imdb_rating="8.0")
        self._add("Above", database_id, imdb_rating="8.1")
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="6.0", maximum="8.0")
        menu_view = submit_interaction.response.edited_view
        pick_button = self._pick_button(menu_view)

        seen = set()
        for _ in range(30):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            seen.add(pick_interaction.response.edited_embed.title)
        self.assertEqual(seen, {"AtMin", "AtMax"})

    async def test_missing_rating_never_matches_an_active_filter(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Rated", database_id, imdb_rating="7.5")
        self._add("Unrated", database_id)
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="0.0")
        menu_view = submit_interaction.response.edited_view
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "Rated")

    async def test_invalid_number_shows_a_clear_error_and_stays_on_the_edit_screen(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, imdb_rating="7.5")
        menu_view = await self._reach_filter_menu(database_id)

        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="not-a-number")

        self.assertIn("Minimum rating", submit_interaction.response.edited_content)
        set_button = next(
            c for c in submit_interaction.response.edited_view.children
            if c.custom_id == "wpm_filter_menu_imdb_rating_set"
        )
        self.assertIsNotNone(set_button)

    async def test_minimum_greater_than_maximum_is_rejected(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, imdb_rating="7.5")
        menu_view = await self._reach_filter_menu(database_id)

        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="9.0", maximum="3.0")

        self.assertIn("cannot be greater than", submit_interaction.response.edited_content)

    async def test_out_of_range_value_is_rejected(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, imdb_rating="7.5")
        menu_view = await self._reach_filter_menu(database_id)

        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="10.5")

        self.assertIn("Minimum rating", submit_interaction.response.edited_content)

    async def test_any_imdb_rating_clears_the_filter(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Low", database_id, imdb_rating="5.5")
        self._add("High", database_id, imdb_rating="8.2")
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="7.0")
        menu_view = submit_interaction.response.edited_view

        menu_view = await self._clear_imdb_rating_filter(menu_view)

        category_select = menu_view.children[0]
        current_values = {option.value: option.label for option in category_select.options}
        self.assertIn("Any IMDb Rating", current_values["imdb_rating"])

    async def test_current_filters_summary_shows_the_active_range(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, imdb_rating="7.5")
        menu_view = await self._reach_filter_menu(database_id)

        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="6.0", maximum="8.0")

        self.assertIn("IMDb Rating: 6.0–8.0", submit_interaction.response.edited_content)


# --- Section 7b: MPAA Rating filter --------------------------------------------------------------


class MpaaRatingFilterTests(RandomWatchCommandTestCase):
    async def test_options_are_derived_from_stored_metadata(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, content_rating="R")
        self._add("Sitcom", database_id, content_rating="PG-13")
        menu_view = await self._reach_filter_menu(database_id)
        menu_view.children[0]._values = [FILTER_CATEGORY_MPAA_RATING]
        mpaa_edit_view = await self._open_category_edit(menu_view)
        mpaa_select = mpaa_edit_view.children[0]

        values = {option.value for option in mpaa_select.options}
        self.assertEqual(values, {ANY_MPAA_RATING_VALUE, "R", "PG-13"})

    async def test_any_mpaa_rating_is_always_the_first_option(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, content_rating="R")
        menu_view = await self._reach_filter_menu(database_id)
        menu_view.children[0]._values = [FILTER_CATEGORY_MPAA_RATING]
        mpaa_edit_view = await self._open_category_edit(menu_view)
        mpaa_select = mpaa_edit_view.children[0]

        self.assertEqual(mpaa_select.options[0].value, ANY_MPAA_RATING_VALUE)

    async def test_matching_is_case_insensitive(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, content_rating="R")
        menu_view = await self._reach_filter_menu(database_id)
        menu_view = await self._set_mpaa_rating_filter(menu_view, "r")
        pick_button = self._pick_button(menu_view)

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertEqual(pick_interaction.response.edited_embed.title, "Alien")

    async def test_not_rated_and_unrated_stay_distinct(self) -> None:
        database_id = self._create_database("Movies")
        self._add("NotRatedMovie", database_id, content_rating="Not Rated")
        self._add("UnratedMovie", database_id, content_rating="Unrated")
        menu_view = await self._reach_filter_menu(database_id)
        menu_view = await self._set_mpaa_rating_filter(menu_view, "Not Rated")
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "NotRatedMovie")

    async def test_missing_rating_never_matches_an_active_filter(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Rated", database_id, content_rating="PG")
        self._add("Unrated", database_id)
        menu_view = await self._reach_filter_menu(database_id)
        menu_view = await self._set_mpaa_rating_filter(menu_view, "PG")
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "Rated")

    async def test_any_mpaa_rating_restores_the_full_pool(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, content_rating="R")
        self._add("Sitcom", database_id, content_rating="PG-13")
        menu_view = await self._reach_filter_menu(database_id)
        menu_view = await self._set_mpaa_rating_filter(menu_view, "R")

        menu_view = await self._clear_mpaa_rating_filter(menu_view)

        category_select = menu_view.children[0]
        current_values = {option.value: option.label for option in category_select.options}
        self.assertIn("Any MPAA Rating", current_values["mpaa_rating"])

    async def test_more_than_twenty_five_ratings_are_capped_safely(self) -> None:
        database_id = self._create_database("Movies")
        for index in range(30):
            self._add(f"Movie {index}", database_id, content_rating=f"Rating{index:02d}")
        menu_view = await self._reach_filter_menu(database_id)
        menu_view.children[0]._values = [FILTER_CATEGORY_MPAA_RATING]
        mpaa_edit_view = await self._open_category_edit(menu_view)
        mpaa_select = mpaa_edit_view.children[0]

        self.assertLessEqual(len(mpaa_select.options), 25)


# --- Section 8b: Actor filter -----------------------------------------------------------------


class ActorFilterTests(RandomWatchCommandTestCase):
    async def test_exact_match_selects_the_actor(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Ace Ventura", database_id, cast=("Jim Carrey", "Courteney Cox"))
        self._add("Unrelated", database_id, cast=("Someone Else",))
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._search_actor_filter(menu_view, "Jim Carrey")
        menu_view = submit_interaction.response.edited_view
        pick_button = self._pick_button(menu_view)

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertEqual(pick_interaction.response.edited_embed.title, "Ace Ventura")

    async def test_partial_case_insensitive_search_with_one_match_auto_applies(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Ace Ventura", database_id, cast=("Jim Carrey",))
        self._add("Unrelated", database_id, cast=("Someone Else",))
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._search_actor_filter(menu_view, "carrey")
        menu_view = submit_interaction.response.edited_view
        pick_button = self._pick_button(menu_view)

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertEqual(pick_interaction.response.edited_embed.title, "Ace Ventura")

    async def test_multiple_matches_show_a_disambiguation_picker(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Movie A", database_id, cast=("Jim Carrey",))
        self._add("Movie B", database_id, cast=("Jimmy Fallon",))
        menu_view = await self._reach_filter_menu(database_id)

        submit_interaction = await self._search_actor_filter(menu_view, "jim")

        match_select = submit_interaction.response.edited_view.children[0]
        values = {option.value for option in match_select.options}
        self.assertEqual(values, {"Jim Carrey", "Jimmy Fallon"})

    async def test_selecting_from_the_disambiguation_picker_applies_that_actor(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Movie A", database_id, cast=("Jim Carrey",))
        self._add("Movie B", database_id, cast=("Jimmy Fallon",))
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._search_actor_filter(menu_view, "jim")
        match_view = submit_interaction.response.edited_view
        match_select = match_view.children[0]
        match_select._values = ["Jim Carrey"]

        select_interaction = FakeInteraction(user=self._member(1))
        await match_select.callback(interaction=select_interaction)

        menu_view = select_interaction.response.edited_view
        pick_button = self._pick_button(menu_view)
        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)
        self.assertEqual(pick_interaction.response.edited_embed.title, "Movie A")

    async def test_no_matches_shows_a_warning_and_stays_in_the_filter_flow(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Movie A", database_id, cast=("Jim Carrey",))
        menu_view = await self._reach_filter_menu(database_id)

        submit_interaction = await self._search_actor_filter(menu_view, "Nobody Real")

        self.assertIn("No actors matching", submit_interaction.response.edited_content)
        search_button = next(
            c for c in submit_interaction.response.edited_view.children
            if c.custom_id == "wpm_filter_menu_actor_search"
        )
        self.assertIsNotNone(search_button)

    async def test_more_than_twenty_five_matches_are_capped_with_a_refine_note(self) -> None:
        database_id = self._create_database("Movies")
        for index in range(30):
            self._add(f"Movie {index}", database_id, cast=(f"Actor Match {index:02d}",))
        menu_view = await self._reach_filter_menu(database_id)

        submit_interaction = await self._search_actor_filter(menu_view, "Actor Match")

        match_view = submit_interaction.response.edited_view
        match_select = match_view.children[0]
        self.assertLessEqual(len(match_select.options), 25)
        self.assertIn("Refine your search", submit_interaction.response.edited_content)

    async def test_missing_cast_metadata_never_matches_an_active_filter(self) -> None:
        database_id = self._create_database("Movies")
        self._add("HasCast", database_id, cast=("Jim Carrey",))
        self._add("NoCast", database_id)
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._search_actor_filter(menu_view, "Jim Carrey")
        menu_view = submit_interaction.response.edited_view
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "HasCast")

    async def test_any_actor_clears_the_filter(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Ace Ventura", database_id, cast=("Jim Carrey",))
        self._add("Unrelated", database_id, cast=("Someone Else",))
        menu_view = await self._reach_filter_menu(database_id)
        submit_interaction = await self._search_actor_filter(menu_view, "Jim Carrey")
        menu_view = submit_interaction.response.edited_view

        menu_view = await self._clear_actor_filter(menu_view)

        category_select = menu_view.children[0]
        current_values = {option.value: option.label for option in category_select.options}
        self.assertIn("Any Actor", current_values["actor"])


# --- Section 10/12: fixed filter order and all-five-combined -------------------------------------


class AllFiveFiltersTests(RandomWatchCommandTestCase):
    async def test_category_select_and_summary_list_all_five_in_the_fixed_order(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        menu_view = await self._reach_filter_menu(database_id)

        category_select = menu_view.children[0]
        self.assertEqual(
            [option.value for option in category_select.options],
            [
                FILTER_CATEGORY_GENRE,
                FILTER_CATEGORY_IMDB_RATING,
                FILTER_CATEGORY_MPAA_RATING,
                FILTER_CATEGORY_ACTOR,
                FILTER_CATEGORY_MEMBER,
            ],
        )

        add_filters_button_interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(add_filters_button_interaction, self.bot)
        initial_view = add_filters_button_interaction.response.sent_view
        add_filters_button = next(c for c in initial_view.children if c.custom_id == "wpm_random_watch_add_filters")
        filters_interaction = FakeInteraction(user=self._member(1))
        await add_filters_button.callback(interaction=filters_interaction)
        body = filters_interaction.response.edited_content

        genre_index = body.index("Genre:")
        imdb_index = body.index("IMDb Rating:")
        mpaa_index = body.index("MPAA Rating:")
        actor_index = body.index("Actor:")
        member_index = body.index("Member:")
        self.assertTrue(genre_index < imdb_index < mpaa_index < actor_index < member_index)

    async def test_all_five_filters_combine_to_a_single_intersection(self) -> None:
        database_id = self._create_database("Movies")
        match = self._add(
            "Perfect Match",
            database_id,
            original_suggester="111",
            genres=("Comedy",),
            imdb_rating="7.5",
            content_rating="PG-13",
            cast=("Jim Carrey",),
        )
        self._add(
            "Wrong Genre",
            database_id,
            original_suggester="111",
            genres=("Horror",),
            imdb_rating="7.5",
            content_rating="PG-13",
            cast=("Jim Carrey",),
        )
        self._add(
            "Wrong Member",
            database_id,
            original_suggester="222",
            genres=("Comedy",),
            imdb_rating="7.5",
            content_rating="PG-13",
            cast=("Jim Carrey",),
        )
        menu_view = await self._reach_filter_menu(database_id)
        menu_view = await self._set_genre_filter(menu_view, "Comedy")
        submit_interaction = await self._set_imdb_rating_filter(menu_view, minimum="7.0", maximum="8.0")
        menu_view = submit_interaction.response.edited_view
        menu_view = await self._set_mpaa_rating_filter(menu_view, "PG-13")
        search_interaction = await self._search_actor_filter(menu_view, "Jim Carrey")
        menu_view = search_interaction.response.edited_view

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())
        pick_button = self._pick_button(menu_view)

        for _ in range(10):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)
            self.assertEqual(pick_interaction.response.edited_embed.title, "Perfect Match")


# --- UI/session behavior ---------------------------------------------------------------------------


class SessionBehaviorTests(RandomWatchCommandTestCase):
    async def test_result_header_includes_collection_and_active_filters(self) -> None:
        header = build_random_watch_result_header("Movies", {"member_display": "KC", "genre": "Horror"})
        self.assertIn("Movies", header)
        self.assertIn("KC", header)
        self.assertIn("Horror", header)

    async def test_result_header_omits_inactive_filters(self) -> None:
        header = build_random_watch_result_header("Movies", {"member_display": None, "genre": None})
        self.assertNotIn("Any Member", header)
        self.assertNotIn("Any Genre", header)

    async def test_result_embed_links_to_the_original_suggestion_post(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, channel_id=CHANNEL_ID, message_id=555)
        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        pick_button = interaction.response.sent_view.children[0]

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        result_view = pick_interaction.response.edited_view
        link_button = next(
            (c for c in result_view.children if getattr(c, "label", None) == "View Original Suggestion"), None
        )
        self.assertIsNotNone(link_button)
        self.assertIn("discord.com/channels", link_button.url)

    async def test_pick_again_preserves_the_active_filters(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        menu_view = await self._reach_filter_menu(database_id)

        class FakeUser:
            id = 111
            display_name = "KC"
            roles = [FakeRole(WATCH_PARTY_ROLE_ID)]

        menu_view, _ = await self._set_member_filter(menu_view, FakeUser())
        pick_button = self._pick_button(menu_view)

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)
        self.assertIn("KC", pick_interaction.response.edited_content)

        result_view = pick_interaction.response.edited_view
        pick_again_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_pick_again")
        again_interaction = FakeInteraction(user=self._member(1))
        await pick_again_button.callback(interaction=again_interaction)

        self.assertIn("KC", again_interaction.response.edited_content)

    async def test_change_filters_keeps_the_collection_and_shows_current_filters(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        pick_button = interaction.response.sent_view.children[0]

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)
        result_view = pick_interaction.response.edited_view
        change_filters_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_change_filters")

        change_interaction = FakeInteraction(user=self._member(1))
        await change_filters_button.callback(interaction=change_interaction)

        # Change Filters, clicked from the now-public result, opens a
        # fresh *ephemeral* message rather than editing the public one --
        # the filter UI must stay private (Section 4).
        self.assertIsInstance(change_interaction.response.sent_view, FilterMenuView)
        self.assertIn("Movies", change_interaction.response.sent_message)
        self.assertTrue(change_interaction.response.sent_ephemeral)

    async def test_change_collection_rebuilds_genre_options_for_the_new_collection(self) -> None:
        first_id = self._create_database("Movies", channel_id=201)
        second_id = self._create_database("TV Shows", channel_id=202)
        self._add("Alien", first_id, genres=("Horror",))
        self._add("Sitcom", second_id, genres=("Comedy",))

        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        # Ambiguous (channel 200 matches neither) -> picker shown.
        picker_view = interaction.response.sent_view
        select = picker_view.children[0]
        select._values = [str(second_id)]
        select_interaction = FakeInteraction(user=self._member(1))
        await select.callback(interaction=select_interaction)

        # The initial command's ambiguous-collection picker always sends a
        # fresh response (edit=False) rather than editing -- unlike the
        # dedicated Change Collection picker, which always edits in place.
        session_view = select_interaction.response.sent_view
        add_filters_button = next(c for c in session_view.children if c.custom_id == "wpm_random_watch_add_filters")
        filters_interaction = FakeInteraction(user=self._member(1))
        await add_filters_button.callback(interaction=filters_interaction)
        menu_view = filters_interaction.response.edited_view
        menu_view.children[0]._values = [FILTER_CATEGORY_GENRE]
        genre_edit_view = await self._open_category_edit(menu_view)
        genre_select = genre_edit_view.children[0]

        self.assertEqual([option.value for option in genre_select.options], ["Comedy"])


# --- Regression -------------------------------------------------------------------------------------


class RegressionTests(RandomWatchCommandTestCase):
    async def test_never_creates_a_voting_round(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        pick_button = interaction.response.sent_view.children[0]

        for _ in range(5):
            pick_interaction = FakeInteraction(user=self._member(1))
            await pick_button.callback(interaction=pick_interaction)

        self.assertIsNone(self.bot.vote_service.get_open_round(database_id))

    async def test_never_modifies_suggestion_status(self) -> None:
        database_id = self._create_database("Movies")
        item = self._add("Alien", database_id)
        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        pick_button = interaction.response.sent_view.children[0]

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)

        self.assertEqual(self.bot.suggestion_service.get_suggestion(item.id).status, WatchItemStatus.SUGGESTED)


# --- Section 4/8: Public result, ephemeral setup, requester-only interaction ----------------------


class PublicResultTests(RandomWatchCommandTestCase):
    async def _pick_from_initial_screen(self, database_id: int):
        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        pick_button = interaction.response.sent_view.children[0]

        pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=pick_interaction)
        return pick_interaction

    async def test_result_is_posted_publicly_via_followup_not_ephemeral(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)

        pick_interaction = await self._pick_from_initial_screen(database_id)

        self.assertFalse(pick_interaction.followup.sent_ephemeral)
        self.assertEqual(pick_interaction.followup.sent_embed.title, "Alien")
        self.assertIsInstance(pick_interaction.followup.sent_view, RandomWatchResultView)

    async def test_setup_screen_is_closed_with_a_private_handoff_not_the_result(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)

        pick_interaction = await self._pick_from_initial_screen(database_id)

        # The interaction's own (ephemeral) response is the hand-off --
        # never the embed/result view itself; those only ever go through
        # the public followup.
        first_edit_content, first_edit_embed, first_edit_view = pick_interaction.response.edit_calls[0]
        self.assertIn("Alien", first_edit_content)
        self.assertIsNone(first_edit_embed)
        self.assertIsNone(first_edit_view)

    async def test_filter_ui_remains_ephemeral(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        interaction = FakeInteraction(user=self._member(1))
        await handle_random_watch(interaction, self.bot)
        initial_view = interaction.response.sent_view
        add_filters_button = next(c for c in initial_view.children if c.custom_id == "wpm_random_watch_add_filters")

        filters_interaction = FakeInteraction(user=self._member(1))
        await add_filters_button.callback(interaction=filters_interaction)

        # /random_watch's own top-level response (interaction.response) is
        # always ephemeral for the setup/filter screens -- only a
        # successfully-found result is ever posted through followup.
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_requester_can_use_the_public_result_buttons(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        pick_interaction = await self._pick_from_initial_screen(database_id)
        result_view = pick_interaction.followup.sent_view

        allowed = await result_view.interaction_check(pick_interaction)

        self.assertTrue(allowed)

    async def test_other_users_cannot_use_the_public_result_buttons(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        pick_interaction = await self._pick_from_initial_screen(database_id)
        result_view = pick_interaction.followup.sent_view
        other_interaction = FakeInteraction(user=self._member(2))

        allowed = await result_view.interaction_check(other_interaction)

        self.assertFalse(allowed)
        self.assertIn("Only the person who ran this command", other_interaction.response.sent_message)
        self.assertTrue(other_interaction.response.sent_ephemeral)

    async def test_requester_id_is_the_member_who_actually_clicked_pick_random_item(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        pick_interaction = await self._pick_from_initial_screen(database_id)
        result_view = pick_interaction.followup.sent_view

        same_user_again = FakeInteraction(user=self._member(1))
        allowed = await result_view.interaction_check(same_user_again)

        self.assertTrue(allowed)

    async def test_pick_again_rerolls_the_public_message_in_place(self) -> None:
        database_id = self._create_database("Movies")
        item_a = self._add("Alien", database_id)
        item_b = self._add("The Matrix", database_id)
        pick_interaction = await self._pick_from_initial_screen(database_id)
        result_view = pick_interaction.followup.sent_view
        pick_again_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_pick_again")

        with patch("watch_party_manager.bot.choose_random_watch_item") as mock_choose:
            mock_choose.return_value = item_b
            again_interaction = FakeInteraction(user=self._member(1))
            await pick_again_button.callback(interaction=again_interaction)

        # A reroll from the already-public result edits in place -- no
        # second public message/followup is created for it.
        self.assertEqual(again_interaction.response.edited_embed.title, "The Matrix")
        self.assertIsNone(again_interaction.followup.sent_embed)

    async def test_change_filters_from_public_opens_a_fresh_ephemeral_message(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id)
        pick_interaction = await self._pick_from_initial_screen(database_id)
        result_view = pick_interaction.followup.sent_view
        change_filters_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_change_filters")

        change_interaction = FakeInteraction(user=self._member(1))
        await change_filters_button.callback(interaction=change_interaction)

        self.assertTrue(change_interaction.response.sent_ephemeral)
        self.assertIsNone(change_interaction.followup.sent_view)

    async def test_change_collection_from_public_opens_a_fresh_ephemeral_message(self) -> None:
        first_id = self._create_database("Movies")
        self._create_database("TV Shows", channel_id=202)
        self._add("Alien", first_id)
        pick_interaction = await self._pick_from_initial_screen(first_id)
        result_view = pick_interaction.followup.sent_view
        change_collection_button = next(
            c for c in result_view.children if c.custom_id == "wpm_random_watch_result_change_collection"
        )

        change_interaction = FakeInteraction(user=self._member(1))
        await change_collection_button.callback(interaction=change_interaction)

        self.assertTrue(change_interaction.response.sent_ephemeral)

    async def test_a_new_pick_reached_via_change_filters_is_also_posted_publicly(self) -> None:
        database_id = self._create_database("Movies")
        self._add("Alien", database_id, original_suggester="111")
        self._add("The Matrix", database_id, original_suggester="222")
        pick_interaction = await self._pick_from_initial_screen(database_id)
        result_view = pick_interaction.followup.sent_view
        change_filters_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_change_filters")
        change_interaction = FakeInteraction(user=self._member(1))
        await change_filters_button.callback(interaction=change_interaction)
        filter_view = change_interaction.response.sent_view
        pick_button = next(c for c in filter_view.children if c.custom_id == "wpm_random_watch_pick_filtered")

        second_pick_interaction = FakeInteraction(user=self._member(1))
        await pick_button.callback(interaction=second_pick_interaction)

        self.assertFalse(second_pick_interaction.followup.sent_ephemeral)
        self.assertIsInstance(second_pick_interaction.followup.sent_view, RandomWatchResultView)

    async def test_public_result_never_creates_a_vote_or_changes_suggestion_state(self) -> None:
        database_id = self._create_database("Movies")
        item = self._add("Alien", database_id)

        pick_interaction = await self._pick_from_initial_screen(database_id)
        result_view = pick_interaction.followup.sent_view
        pick_again_button = next(c for c in result_view.children if c.custom_id == "wpm_random_watch_pick_again")
        await pick_again_button.callback(interaction=FakeInteraction(user=self._member(1)))

        self.assertIsNone(self.bot.vote_service.get_open_round(database_id))
        self.assertEqual(self.bot.suggestion_service.get_suggestion(item.id).status, WatchItemStatus.SUGGESTED)
        self.assertIsNone(self.bot.suggestion_database_configuration_repository.get(GUILD_ID, database_id))


if __name__ == "__main__":
    unittest.main()
