"""Tests for filter_menu_view.py's Discord UI components: construction,
stable custom_ids/labels, and forwarding clicks/selections/modal
submissions to the supplied callback. All session/business logic lives
in bot.py's create_filter_menu_session, shared by /vote start's Custom
Vote Filters and /random watch.
"""

import unittest

import discord

from watch_party_manager.filter_menu_view import (
    FILTER_CATEGORY_ACTOR,
    FILTER_CATEGORY_GENRE,
    FILTER_CATEGORY_IMDB_RATING,
    FILTER_CATEGORY_MEMBER,
    FILTER_CATEGORY_MPAA_RATING,
    FILTER_CATEGORY_ORDER,
    FILTER_MENU_VIEW_TIMEOUT_SECONDS,
    ActorEditView,
    ActorMatchEditView,
    ActorSearchModal,
    BackToFilterMenuButton,
    ClearFilterButton,
    FilterCategorySelectComponent,
    FilterMenuView,
    GenreEditView,
    ImdbRatingEditView,
    ImdbRatingModal,
    MemberEditView,
    MpaaRatingEditView,
    format_current_filters_block,
)


async def _noop(*args, **kwargs):
    return None


class FilterCategorySelectComponentTests(unittest.IsolatedAsyncioTestCase):
    def test_offers_options_in_the_shared_fixed_order(self) -> None:
        select = FilterCategorySelectComponent(_noop, current_values={})
        self.assertEqual([option.value for option in select.options], list(FILTER_CATEGORY_ORDER))

    def test_member_is_the_last_option(self) -> None:
        select = FilterCategorySelectComponent(_noop, current_values={})
        self.assertEqual(select.options[-1].value, FILTER_CATEGORY_MEMBER)

    def test_option_labels_show_the_current_value(self) -> None:
        select = FilterCategorySelectComponent(_noop, current_values={FILTER_CATEGORY_GENRE: "Comedy"})
        genre_option = next(o for o in select.options if o.value == FILTER_CATEGORY_GENRE)
        self.assertIn("Comedy", genre_option.label)

    def test_option_label_shows_any_when_no_current_value(self) -> None:
        select = FilterCategorySelectComponent(_noop, current_values={})
        genre_option = next(o for o in select.options if o.value == FILTER_CATEGORY_GENRE)
        self.assertIn("Genre", genre_option.label)

    async def test_callback_forwards_the_selected_category(self) -> None:
        calls = []

        async def on_selected(interaction, category) -> None:
            calls.append(category)

        select = FilterCategorySelectComponent(on_selected, current_values={})
        select._values = [FILTER_CATEGORY_ACTOR]
        await select.callback(interaction=object())
        self.assertEqual(calls, [FILTER_CATEGORY_ACTOR])


class FormatCurrentFiltersBlockTests(unittest.TestCase):
    """The shared, Discord-friendly "Current Filters" presentation (UX
    polish: dot-leader alignment) -- used by both bot.py's
    build_current_filters_summary and, per Section 9, reusable as-is by
    any future consumer of the shared filter menu (e.g. /browse).
    """

    def test_uses_the_fixed_filter_order(self) -> None:
        block = format_current_filters_block({})
        lines = block.split("\n")[2:]  # skip the header and blank line
        labels_in_order = [line.split(" ")[0] for line in lines]
        # "IMDb Rating" and "MPAA Rating" are two words each, but the
        # first word alone is still unique and order-preserving here.
        self.assertEqual(labels_in_order, ["Genre", "IMDb", "MPAA", "Actor", "Member"])

    def test_header_is_bolded_and_followed_by_a_blank_line(self) -> None:
        block = format_current_filters_block({})
        lines = block.split("\n")
        self.assertEqual(lines[0], "**Current Filters**")
        self.assertEqual(lines[1], "")

    def test_custom_header_is_respected(self) -> None:
        block = format_current_filters_block({}, header="Browse Filters")
        self.assertEqual(block.split("\n")[0], "**Browse Filters**")

    def test_missing_value_falls_back_to_any(self) -> None:
        block = format_current_filters_block({})
        self.assertIn("Genre", block)
        self.assertIn("Any", block)

    def test_active_value_is_shown_verbatim(self) -> None:
        block = format_current_filters_block({FILTER_CATEGORY_GENRE: "Sci-Fi"})
        genre_line = next(line for line in block.split("\n") if line.startswith("Genre"))
        self.assertIn("Sci-Fi", genre_line)

    def test_labels_are_dot_leader_aligned(self) -> None:
        block = format_current_filters_block(
            {
                FILTER_CATEGORY_GENRE: "Sci-Fi",
                FILTER_CATEGORY_IMDB_RATING: "7.0+",
                FILTER_CATEGORY_MPAA_RATING: "PG-13",
                FILTER_CATEGORY_ACTOR: "Jim Carrey",
                FILTER_CATEGORY_MEMBER: "KC",
            }
        )
        self.assertIn("Genre .......... Sci-Fi", block)
        self.assertIn("IMDb Rating .... 7.0+", block)
        self.assertIn("MPAA Rating .... PG-13", block)
        self.assertIn("Actor .......... Jim Carrey", block)
        self.assertIn("Member ......... KC", block)

    def test_every_line_has_at_least_a_minimal_dot_leader(self) -> None:
        # Guards against a future, much-longer filter label collapsing
        # the leader to nothing or going negative.
        block = format_current_filters_block({})
        for line in block.split("\n")[2:]:
            self.assertIn("...", line)


class ClearFilterButtonTests(unittest.IsolatedAsyncioTestCase):
    """UX polish (Section 1): every filter editor's reset action is now
    this one consistently-worded, consistently-styled button, replacing
    five previously differently-worded resets.
    """

    def test_label_is_clear_filter(self) -> None:
        button = ClearFilterButton(_noop, custom_id="wpm_test_clear")
        self.assertEqual(button.label, "Clear Filter")

    def test_style_is_secondary(self) -> None:
        button = ClearFilterButton(_noop, custom_id="wpm_test_clear")
        self.assertEqual(button.style, discord.ButtonStyle.secondary)

    async def test_callback_triggers_its_callback(self) -> None:
        calls = []

        async def on_click(interaction) -> None:
            calls.append("cleared")

        button = ClearFilterButton(on_click, custom_id="wpm_test_clear")
        await button.callback(interaction=object())
        self.assertEqual(calls, ["cleared"])


class FilterMenuViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_category_select_and_primary_button(self) -> None:
        view = FilterMenuView(
            _noop, _noop, current_values={}, primary_action_label="Pick Random Item", primary_action_custom_id="wpm_test_primary"
        )
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.timeout, FILTER_MENU_VIEW_TIMEOUT_SECONDS)

    def test_includes_secondary_action_when_provided(self) -> None:
        view = FilterMenuView(
            _noop,
            _noop,
            current_values={},
            primary_action_label="Pick Random Item",
            primary_action_custom_id="wpm_test_primary",
            on_secondary_action=_noop,
            secondary_action_label="Change Collection",
        )
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.children[2].label, "Change Collection")

    def test_primary_button_disabled_when_requested(self) -> None:
        view = FilterMenuView(
            _noop,
            _noop,
            current_values={},
            primary_action_label="Pick Random Item",
            primary_action_custom_id="wpm_test_primary",
            primary_action_disabled=True,
        )
        self.assertTrue(view.primary_button.disabled)

    async def test_category_select_triggers_its_callback(self) -> None:
        calls = []

        async def on_category_selected(interaction, category) -> None:
            calls.append(category)

        view = FilterMenuView(
            on_category_selected,
            _noop,
            current_values={},
            primary_action_label="Pick Random Item",
            primary_action_custom_id="wpm_test_primary",
        )
        view.children[0]._values = [FILTER_CATEGORY_GENRE]
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, [FILTER_CATEGORY_GENRE])

    async def test_primary_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_primary(interaction) -> None:
            calls.append("primary")

        view = FilterMenuView(
            _noop, on_primary, current_values={}, primary_action_label="Pick Random Item", primary_action_custom_id="wpm_test_primary"
        )
        await view.primary_button.callback(interaction=object())
        self.assertEqual(calls, ["primary"])


class GenreEditViewTests(unittest.IsolatedAsyncioTestCase):
    """Genre's editor: Select, Clear Filter, Back -- the standardized
    3-row shape every filter editor now shares (Section 3).
    """

    def test_has_genre_select_clear_filter_and_back_button(self) -> None:
        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = GenreEditView(_noop, _noop, options=options)
        self.assertEqual(len(view.children), 3)
        self.assertIsInstance(view.children[1], ClearFilterButton)
        self.assertIsInstance(view.children[2], BackToFilterMenuButton)

    def test_clear_filter_button_is_labeled_consistently(self) -> None:
        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = GenreEditView(_noop, _noop, options=options)
        self.assertEqual(view.children[1].label, "Clear Filter")

    def test_genre_select_allows_clearing(self) -> None:
        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = GenreEditView(_noop, _noop, options=options)
        self.assertEqual(view.children[0].min_values, 0)

    async def test_genre_select_callback_forwards_none_when_cleared(self) -> None:
        calls = []

        async def on_change(interaction, genre) -> None:
            calls.append(genre)

        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = GenreEditView(on_change, _noop, options=options)
        view.children[0]._values = []
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, [None])

    async def test_clear_filter_button_clears_the_currently_edited_filter_only(self) -> None:
        calls = []

        async def on_change(interaction, genre) -> None:
            calls.append(genre)

        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = GenreEditView(on_change, _noop, options=options)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, [None])

    async def test_back_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append("back")

        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = GenreEditView(_noop, on_back, options=options)
        await view.children[2].callback(interaction=object())
        self.assertEqual(calls, ["back"])


class MpaaRatingEditViewTests(unittest.IsolatedAsyncioTestCase):
    """MPAA Rating's editor (Section 6): now matches Genre/Member's own
    select shape (min_values=0, no inline "Any" option) plus the same
    standalone Clear Filter button every other editor uses.
    """

    def test_has_mpaa_select_clear_filter_and_back_button(self) -> None:
        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(_noop, _noop, options=options)
        self.assertEqual(len(view.children), 3)
        self.assertIsInstance(view.children[1], ClearFilterButton)
        self.assertIsInstance(view.children[2], BackToFilterMenuButton)

    def test_clear_filter_button_is_labeled_consistently(self) -> None:
        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(_noop, _noop, options=options)
        self.assertEqual(view.children[1].label, "Clear Filter")

    def test_select_allows_clearing_like_genre_and_member(self) -> None:
        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(_noop, _noop, options=options)
        self.assertEqual(view.children[0].min_values, 0)
        self.assertEqual(view.children[0].max_values, 1)

    def test_select_only_offers_the_actual_ratings_no_inline_any_option(self) -> None:
        options = [discord.SelectOption(label="PG-13", value="PG-13"), discord.SelectOption(label="R", value="R")]
        view = MpaaRatingEditView(_noop, _noop, options=options)
        self.assertEqual([option.value for option in view.children[0].options], ["PG-13", "R"])

    async def test_selecting_a_rating_forwards_it(self) -> None:
        calls = []

        async def on_change(interaction, rating) -> None:
            calls.append(rating)

        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(on_change, _noop, options=options)
        view.children[0]._values = ["PG-13"]
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["PG-13"])

    async def test_clearing_the_select_natively_forwards_none(self) -> None:
        calls = []

        async def on_change(interaction, rating) -> None:
            calls.append(rating)

        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(on_change, _noop, options=options)
        view.children[0]._values = []
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, [None])

    async def test_clear_filter_button_also_forwards_none(self) -> None:
        calls = []

        async def on_change(interaction, rating) -> None:
            calls.append(rating)

        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(on_change, _noop, options=options)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, [None])


class MemberEditViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_member_userselect_clear_filter_and_back_button(self) -> None:
        view = MemberEditView(_noop, _noop)
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.children[0].min_values, 0)
        self.assertIsInstance(view.children[1], ClearFilterButton)
        self.assertIsInstance(view.children[2], BackToFilterMenuButton)

    def test_clear_filter_button_is_labeled_consistently(self) -> None:
        view = MemberEditView(_noop, _noop)
        self.assertEqual(view.children[1].label, "Clear Filter")

    async def test_userselect_callback_forwards_none_when_cleared(self) -> None:
        calls = []

        async def on_change(interaction, member) -> None:
            calls.append(member)

        view = MemberEditView(on_change, _noop)
        view.children[0]._values = []
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, [None])

    async def test_clear_filter_button_also_forwards_none(self) -> None:
        calls = []

        async def on_change(interaction, member) -> None:
            calls.append(member)

        view = MemberEditView(on_change, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, [None])


class ImdbRatingEditViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_set_clear_filter_and_back_buttons(self) -> None:
        view = ImdbRatingEditView(_noop, _noop, _noop)
        labels = [child.label for child in view.children]
        self.assertEqual(labels, ["Set Rating Range", "Clear Filter", "Back to Filters"])

    async def test_set_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_set(interaction) -> None:
            calls.append("set")

        view = ImdbRatingEditView(on_set, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["set"])

    async def test_clear_filter_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_clear(interaction) -> None:
            calls.append("cleared")

        view = ImdbRatingEditView(_noop, on_clear, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["cleared"])


class ImdbRatingModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_on_submit_forwards_both_values(self) -> None:
        calls = []

        async def on_submit(interaction, minimum, maximum) -> None:
            calls.append((minimum, maximum))

        modal = ImdbRatingModal(on_submit)
        modal.minimum_input._value = "7.0"
        modal.maximum_input._value = "9.0"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, [("7.0", "9.0")])

    async def test_blank_fields_forward_none(self) -> None:
        calls = []

        async def on_submit(interaction, minimum, maximum) -> None:
            calls.append((minimum, maximum))

        modal = ImdbRatingModal(on_submit)
        modal.minimum_input._value = ""
        modal.maximum_input._value = ""
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, [(None, None)])

    def test_prefills_defaults(self) -> None:
        modal = ImdbRatingModal(_noop, default_minimum="6.0", default_maximum="8.0")
        self.assertEqual(modal.minimum_input.default, "6.0")
        self.assertEqual(modal.maximum_input.default, "8.0")

    def test_field_labels_name_imdb_rating_explicitly(self) -> None:
        modal = ImdbRatingModal(_noop)
        self.assertIn("IMDb Rating", modal.minimum_input.label)
        self.assertIn("Minimum", modal.minimum_input.label)
        self.assertIn("IMDb Rating", modal.maximum_input.label)
        self.assertIn("Maximum", modal.maximum_input.label)


class ActorEditViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_search_clear_filter_and_back_buttons(self) -> None:
        view = ActorEditView(_noop, _noop, _noop)
        labels = [child.label for child in view.children]
        self.assertEqual(labels, ["Search Actor", "Clear Filter", "Back to Filters"])

    async def test_search_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_search(interaction) -> None:
            calls.append("search")

        view = ActorEditView(on_search, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["search"])

    async def test_clear_filter_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_clear(interaction) -> None:
            calls.append("cleared")

        view = ActorEditView(_noop, on_clear, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["cleared"])


class ActorSearchModalTests(unittest.IsolatedAsyncioTestCase):
    def test_title_matches_the_action_button_wording(self) -> None:
        modal = ActorSearchModal(_noop)
        self.assertEqual(modal.title, "Search Actor")

    async def test_on_submit_forwards_the_query(self) -> None:
        calls = []

        async def on_submit(interaction, query) -> None:
            calls.append(query)

        modal = ActorSearchModal(on_submit)
        modal.query_input._value = "Jim Carrey"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, ["Jim Carrey"])

    async def test_blank_query_forwards_none(self) -> None:
        calls = []

        async def on_submit(interaction, query) -> None:
            calls.append(query)

        modal = ActorSearchModal(on_submit)
        modal.query_input._value = ""
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, [None])


class ActorMatchEditViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_match_select_and_back_button(self) -> None:
        options = [discord.SelectOption(label="Jim Carrey", value="Jim Carrey")]
        view = ActorMatchEditView(_noop, _noop, options=options)
        self.assertEqual(len(view.children), 2)
        self.assertIsInstance(view.children[1], BackToFilterMenuButton)

    async def test_selecting_an_actor_forwards_it(self) -> None:
        calls = []

        async def on_selected(interaction, actor) -> None:
            calls.append(actor)

        options = [discord.SelectOption(label="Jim Carrey", value="Jim Carrey")]
        view = ActorMatchEditView(on_selected, _noop, options=options)
        view.children[0]._values = ["Jim Carrey"]
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["Jim Carrey"])


if __name__ == "__main__":
    unittest.main()
