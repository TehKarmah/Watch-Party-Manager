"""Tests for WatchPartySelectView (Release Polish: Discord-native UX),
used by /cancel_watch_party and /reschedule_watch_party.
"""

from __future__ import annotations

import unittest

from watch_party_manager.watch_party_selection_view import (
    WATCH_PARTY_SELECTION_VIEW_TIMEOUT_SECONDS,
    WatchPartySelectView,
)


async def _noop(interaction, value) -> None:
    pass


class WatchPartySelectViewTests(unittest.IsolatedAsyncioTestCase):
    def _view(self, options, on_select=_noop):
        return WatchPartySelectView(
            options, on_select, custom_id="wpm_test_watch_party_select", placeholder="Choose..."
        )

    async def test_has_one_select(self) -> None:
        view = self._view([(1, "The Matrix", "Scheduled 2026-08-01 20:00 UTC")])
        self.assertEqual(1, len(view.children))

    async def test_builds_one_option_per_watch_party_with_a_description(self) -> None:
        options = [
            (1, "The Matrix", "Scheduled 2026-08-01 20:00 UTC"),
            (2, "Inception", "Scheduled 2026-09-01 20:00 UTC"),
        ]
        view = self._view(options)
        select = view.children[0]
        self.assertEqual(["1", "2"], [option.value for option in select.options])
        self.assertEqual(["The Matrix", "Inception"], [option.label for option in select.options])
        self.assertEqual(
            ["Scheduled 2026-08-01 20:00 UTC", "Scheduled 2026-09-01 20:00 UTC"],
            [option.description for option in select.options],
        )

    async def test_caps_options_at_twenty_five(self) -> None:
        options = [(i, f"Watch Party {i}", "Scheduled 2026-08-01 20:00 UTC") for i in range(1, 31)]
        view = self._view(options)
        self.assertEqual(25, len(view.children[0].options))

    async def test_truncates_long_labels_and_descriptions(self) -> None:
        view = self._view([(1, "x" * 150, "y" * 150)])
        select = view.children[0]
        self.assertEqual(100, len(select.options[0].label))
        self.assertEqual(100, len(select.options[0].description))

    async def test_callback_forwards_the_chosen_watch_party_id(self) -> None:
        calls = []

        async def on_select(interaction, watch_party_id) -> None:
            calls.append(watch_party_id)

        view = self._view(
            [
                (1, "The Matrix", "Scheduled 2026-08-01 20:00 UTC"),
                (2, "Inception", "Scheduled 2026-09-01 20:00 UTC"),
            ],
            on_select,
        )
        select = view.children[0]
        select._values = ["2"]

        await select.callback(interaction=object())

        self.assertEqual([2], calls)

    async def test_uses_its_own_selection_view_timeout(self) -> None:
        view = self._view([(1, "The Matrix", "Scheduled 2026-08-01 20:00 UTC")])
        self.assertEqual(WATCH_PARTY_SELECTION_VIEW_TIMEOUT_SECONDS, view.timeout)


if __name__ == "__main__":
    unittest.main()
