"""Tests for random_watch_view.py's Discord UI components: construction,
stable custom_ids/labels, and forwarding clicks/selections to the
supplied callback. All session/business logic lives in bot.py.
"""

import unittest

import discord

from watch_party_manager.random_watch_view import (
    RANDOM_WATCH_VIEW_TIMEOUT_SECONDS,
    RandomWatchFilterView,
    RandomWatchGenreFilterSelectComponent,
    RandomWatchInitialView,
    RandomWatchMemberFilterSelectComponent,
    RandomWatchResultView,
)


async def _noop(*args, **kwargs):
    return None


class RandomWatchInitialViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_pick_random_and_add_filters_buttons(self) -> None:
        view = RandomWatchInitialView(_noop, _noop)
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Pick Random Item", "wpm_random_watch_pick_initial"),
                ("Add Filters", "wpm_random_watch_add_filters"),
            ],
        )
        self.assertEqual(view.timeout, RANDOM_WATCH_VIEW_TIMEOUT_SECONDS)

    async def test_pick_random_button_uses_primary_style(self) -> None:
        view = RandomWatchInitialView(_noop, _noop)
        self.assertEqual(view.children[0].style, discord.ButtonStyle.primary)

    async def test_pick_random_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_pick(interaction) -> None:
            calls.append("pick")

        view = RandomWatchInitialView(on_pick, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["pick"])

    async def test_add_filters_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_add_filters(interaction) -> None:
            calls.append("filters")

        view = RandomWatchInitialView(_noop, on_add_filters)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["filters"])


class RandomWatchMemberFilterSelectComponentTests(unittest.IsolatedAsyncioTestCase):
    def test_allows_clearing_via_min_values_zero(self) -> None:
        select = RandomWatchMemberFilterSelectComponent(_noop)
        self.assertEqual(select.min_values, 0)
        self.assertEqual(select.max_values, 1)
        self.assertEqual(select.custom_id, "wpm_random_watch_member_filter")

    async def test_callback_forwards_none_when_cleared(self) -> None:
        calls = []

        async def on_change(interaction, member) -> None:
            calls.append(member)

        select = RandomWatchMemberFilterSelectComponent(on_change)
        select._values = []
        await select.callback(interaction=object())
        self.assertEqual(calls, [None])

    async def test_callback_forwards_the_selected_member(self) -> None:
        calls = []

        async def on_change(interaction, member) -> None:
            calls.append(member)

        select = RandomWatchMemberFilterSelectComponent(on_change)
        select._values = ["fake-member"]
        await select.callback(interaction=object())
        self.assertEqual(calls, ["fake-member"])


class RandomWatchGenreFilterSelectComponentTests(unittest.IsolatedAsyncioTestCase):
    def test_allows_clearing_via_min_values_zero(self) -> None:
        options = [discord.SelectOption(label="Horror", value="Horror")]
        select = RandomWatchGenreFilterSelectComponent(_noop, options=options)
        self.assertEqual(select.min_values, 0)
        self.assertEqual(select.custom_id, "wpm_random_watch_genre_filter")

    async def test_callback_forwards_the_selected_genre(self) -> None:
        calls = []

        async def on_change(interaction, genre) -> None:
            calls.append(genre)

        options = [discord.SelectOption(label="Horror", value="Horror")]
        select = RandomWatchGenreFilterSelectComponent(on_change, options=options)
        select._values = ["Horror"]
        await select.callback(interaction=object())
        self.assertEqual(calls, ["Horror"])


class RandomWatchFilterViewTests(unittest.IsolatedAsyncioTestCase):
    def test_includes_member_select_genre_select_pick_and_change_collection(self) -> None:
        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = RandomWatchFilterView(_noop, _noop, _noop, _noop, genre_filter_options=options)
        custom_ids = [child.custom_id for child in view.children]
        self.assertEqual(
            custom_ids,
            [
                "wpm_random_watch_member_filter",
                "wpm_random_watch_genre_filter",
                "wpm_random_watch_pick_filtered",
                "wpm_random_watch_change_collection",
            ],
        )

    def test_omits_the_genre_select_when_no_genres_are_available(self) -> None:
        view = RandomWatchFilterView(_noop, _noop, _noop, _noop, genre_filter_options=[])
        custom_ids = [child.custom_id for child in view.children]
        self.assertNotIn("wpm_random_watch_genre_filter", custom_ids)
        self.assertEqual(
            custom_ids, ["wpm_random_watch_member_filter", "wpm_random_watch_pick_filtered", "wpm_random_watch_change_collection"]
        )

    async def test_pick_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_pick(interaction) -> None:
            calls.append("pick")

        view = RandomWatchFilterView(_noop, _noop, on_pick, _noop, genre_filter_options=[])
        pick_button = next(c for c in view.children if c.custom_id == "wpm_random_watch_pick_filtered")
        await pick_button.callback(interaction=object())
        self.assertEqual(calls, ["pick"])

    async def test_change_collection_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_change_collection(interaction) -> None:
            calls.append("change")

        view = RandomWatchFilterView(_noop, _noop, _noop, on_change_collection, genre_filter_options=[])
        button = next(c for c in view.children if c.custom_id == "wpm_random_watch_change_collection")
        await button.callback(interaction=object())
        self.assertEqual(calls, ["change"])

    def test_row_count_stays_within_discord_limits(self) -> None:
        options = [discord.SelectOption(label="Horror", value="Horror")]
        view = RandomWatchFilterView(_noop, _noop, _noop, _noop, genre_filter_options=options)
        # Every component here is single-row (Select/UserSelect/Button),
        # so component count doubles as a row-count proxy; must stay <=5.
        self.assertLessEqual(len(view.children), 5)

    def test_pick_button_is_enabled_by_default(self) -> None:
        view = RandomWatchFilterView(_noop, _noop, _noop, _noop, genre_filter_options=[])
        self.assertFalse(view.pick_button.disabled)

    def test_pick_button_is_disabled_when_member_filter_invalid(self) -> None:
        view = RandomWatchFilterView(_noop, _noop, _noop, _noop, genre_filter_options=[], member_filter_invalid=True)
        self.assertTrue(view.pick_button.disabled)


class RandomWatchResultViewTests(unittest.IsolatedAsyncioTestCase):
    def test_includes_pick_again_change_filters_and_change_collection(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop)
        custom_ids = [child.custom_id for child in view.children]
        self.assertEqual(
            custom_ids,
            [
                "wpm_random_watch_pick_again",
                "wpm_random_watch_change_filters",
                "wpm_random_watch_result_change_collection",
            ],
        )

    def test_omits_the_link_button_when_no_original_suggestion_url(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop, original_suggestion_url=None)
        labels = [getattr(child, "label", None) for child in view.children]
        self.assertNotIn("View Original Suggestion", labels)

    def test_includes_a_link_style_button_when_a_url_is_available(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop, original_suggestion_url="https://discord.com/channels/1/2/3")
        link_button = next(c for c in view.children if getattr(c, "label", None) == "View Original Suggestion")
        self.assertEqual(link_button.style, discord.ButtonStyle.link)
        self.assertEqual(link_button.url, "https://discord.com/channels/1/2/3")

    async def test_pick_again_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_pick_again(interaction) -> None:
            calls.append("again")

        view = RandomWatchResultView(on_pick_again, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["again"])

    async def test_change_filters_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_change_filters(interaction) -> None:
            calls.append("filters")

        view = RandomWatchResultView(_noop, on_change_filters, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["filters"])

    async def test_change_collection_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_change_collection(interaction) -> None:
            calls.append("collection")

        view = RandomWatchResultView(_noop, _noop, on_change_collection)
        await view.children[2].callback(interaction=object())
        self.assertEqual(calls, ["collection"])

    def test_row_count_stays_within_discord_limits_with_link_button(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop, original_suggestion_url="https://discord.com/channels/1/2/3")
        self.assertLessEqual(len(view.children), 5)


if __name__ == "__main__":
    unittest.main()
