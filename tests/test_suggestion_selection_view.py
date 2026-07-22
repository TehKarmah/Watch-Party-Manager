"""Tests for FR-033A's /list database picker and /remove match picker."""

from __future__ import annotations

import unittest

from watch_party_manager.suggestion_selection_view import (
    ListDatabaseSelectView,
    RemovalMatchSelectView,
)


async def _noop(interaction, value) -> None:
    pass


class ListDatabaseSelectViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_one_select(self) -> None:
        view = ListDatabaseSelectView([(1, "Movie Night"), (2, "Anime Night")], _noop)
        self.assertEqual(1, len(view.children))

    async def test_builds_one_option_per_database(self) -> None:
        view = ListDatabaseSelectView([(1, "Movie Night"), (2, "Anime Night")], _noop)
        select = view.children[0]
        self.assertEqual(["1", "2"], [option.value for option in select.options])
        self.assertEqual(["Movie Night", "Anime Night"], [option.label for option in select.options])

    async def test_caps_options_at_twenty_five(self) -> None:
        databases = [(i, f"DB {i}") for i in range(1, 31)]
        view = ListDatabaseSelectView(databases, _noop)
        self.assertEqual(25, len(view.children[0].options))

    async def test_callback_forwards_the_chosen_database_id(self) -> None:
        calls = []

        async def on_select(interaction, database_id) -> None:
            calls.append(database_id)

        view = ListDatabaseSelectView([(1, "Movie Night"), (2, "Anime Night")], on_select)
        select = view.children[0]
        select._values = ["2"]

        await select.callback(interaction=object())

        self.assertEqual([2], calls)


class RemovalMatchSelectViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_one_select(self) -> None:
        view = RemovalMatchSelectView([(1, "#0001 - Alien (1979)")], _noop)
        self.assertEqual(1, len(view.children))

    async def test_builds_one_option_per_match(self) -> None:
        matches = [(1, "#0001 - Alien (1979)"), (2, "#0002 - Alien (1979)")]
        view = RemovalMatchSelectView(matches, _noop)
        select = view.children[0]
        self.assertEqual(["1", "2"], [option.value for option in select.options])

    async def test_truncates_long_labels(self) -> None:
        long_label = "x" * 150
        view = RemovalMatchSelectView([(1, long_label)], _noop)
        self.assertEqual(100, len(view.children[0].options[0].label))

    async def test_callback_forwards_the_chosen_suggestion_id(self) -> None:
        calls = []

        async def on_select(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        view = RemovalMatchSelectView([(1, "one"), (2, "two")], on_select)
        select = view.children[0]
        select._values = ["1"]

        await select.callback(interaction=object())

        self.assertEqual([1], calls)


if __name__ == "__main__":
    unittest.main()
