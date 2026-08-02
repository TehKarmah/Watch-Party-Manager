"""Tests for filter_menu_view.py's Discord UI components: construction,
stable custom_ids/labels, and forwarding clicks/selections/modal
submissions to the supplied callback. All session/business logic lives
in bot.py's create_filter_menu_session, shared by /vote start's Custom
Vote Filters and /random watch.
"""

import unittest

import discord

from watch_party_manager.filter_menu_view import (
    ANY_MPAA_RATING_VALUE,
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
    FilterCategorySelectComponent,
    FilterMenuView,
    GenreEditView,
    ImdbRatingEditView,
    ImdbRatingModal,
    MemberEditView,
    MpaaRatingEditView,
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
    def test_has_genre_select_and_back_button(self) -> None:
        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = GenreEditView(_noop, _noop, options=options)
        self.assertEqual(len(view.children), 2)
        self.assertIsInstance(view.children[1], BackToFilterMenuButton)

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

    async def test_back_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append("back")

        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = GenreEditView(_noop, on_back, options=options)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["back"])


class MpaaRatingEditViewTests(unittest.IsolatedAsyncioTestCase):
    def test_any_mpaa_rating_is_the_first_option(self) -> None:
        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(_noop, _noop, options=options)
        select = view.children[0]
        self.assertEqual(select.options[0].value, ANY_MPAA_RATING_VALUE)
        self.assertEqual(select.options[0].label, "Any MPAA Rating")

    def test_select_always_requires_exactly_one_value(self) -> None:
        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(_noop, _noop, options=options)
        self.assertEqual(view.children[0].min_values, 1)
        self.assertEqual(view.children[0].max_values, 1)

    async def test_selecting_any_forwards_none(self) -> None:
        calls = []

        async def on_change(interaction, rating) -> None:
            calls.append(rating)

        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(on_change, _noop, options=options)
        view.children[0]._values = [ANY_MPAA_RATING_VALUE]
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, [None])

    async def test_selecting_a_rating_forwards_it(self) -> None:
        calls = []

        async def on_change(interaction, rating) -> None:
            calls.append(rating)

        options = [discord.SelectOption(label="PG-13", value="PG-13")]
        view = MpaaRatingEditView(on_change, _noop, options=options)
        view.children[0]._values = ["PG-13"]
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["PG-13"])


class MemberEditViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_member_userselect_and_back_button(self) -> None:
        view = MemberEditView(_noop, _noop)
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.children[0].min_values, 0)
        self.assertIsInstance(view.children[1], BackToFilterMenuButton)

    async def test_userselect_callback_forwards_none_when_cleared(self) -> None:
        calls = []

        async def on_change(interaction, member) -> None:
            calls.append(member)

        view = MemberEditView(on_change, _noop)
        view.children[0]._values = []
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, [None])


class ImdbRatingEditViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_set_any_and_back_buttons(self) -> None:
        view = ImdbRatingEditView(_noop, _noop, _noop)
        labels = [child.label for child in view.children]
        self.assertEqual(labels, ["Set Minimum/Maximum...", "Any IMDb Rating", "Back to Filters"])

    async def test_set_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_set(interaction) -> None:
            calls.append("set")

        view = ImdbRatingEditView(on_set, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["set"])

    async def test_any_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_any(interaction) -> None:
            calls.append("any")

        view = ImdbRatingEditView(_noop, on_any, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["any"])


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


class ActorEditViewTests(unittest.IsolatedAsyncioTestCase):
    def test_has_search_any_and_back_buttons(self) -> None:
        view = ActorEditView(_noop, _noop, _noop)
        labels = [child.label for child in view.children]
        self.assertEqual(labels, ["Search for an Actor...", "Any Actor", "Back to Filters"])

    async def test_search_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_search(interaction) -> None:
            calls.append("search")

        view = ActorEditView(on_search, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["search"])

    async def test_any_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_any(interaction) -> None:
            calls.append("any")

        view = ActorEditView(_noop, on_any, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["any"])


class ActorSearchModalTests(unittest.IsolatedAsyncioTestCase):
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
