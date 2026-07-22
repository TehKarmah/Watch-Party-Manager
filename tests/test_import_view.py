"""Tests for FR-032C's /import mode-choice Discord UI."""

from __future__ import annotations

import unittest

from watch_party_manager.import_view import ImportModeChoiceView
from watch_party_manager.type_to_confirm_view import TypeToConfirmModal


class FakeResponse:
    def __init__(self) -> None:
        self.sent_modal = None

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponse()


async def _noop(interaction) -> None:
    pass


class ImportModeChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    def _build_view(self, on_merge=None, on_replace=None, on_cancel=None) -> ImportModeChoiceView:
        return ImportModeChoiceView(
            on_merge=on_merge or _noop,
            on_replace=on_replace or _noop,
            on_cancel=on_cancel or _noop,
        )

    async def test_has_three_buttons(self) -> None:
        view = self._build_view()
        self.assertEqual(len(view.children), 3)

    async def test_button_labels_and_order(self) -> None:
        view = self._build_view()
        self.assertEqual(view.children[0].label, "Merge")
        self.assertEqual(view.children[1].label, "Replace")
        self.assertEqual(view.children[2].label, "Cancel")

    async def test_merge_click_forwards_directly_to_on_merge(self) -> None:
        calls = []

        async def on_merge(interaction) -> None:
            calls.append(interaction)

        view = self._build_view(on_merge=on_merge)
        interaction = FakeInteraction()

        await view.children[0].callback(interaction)

        self.assertEqual(calls, [interaction])

    async def test_replace_click_opens_the_typed_confirmation_modal(self) -> None:
        view = self._build_view()
        interaction = FakeInteraction()

        await view.children[1].callback(interaction)

        self.assertIsInstance(interaction.response.sent_modal, TypeToConfirmModal)

    async def test_cancel_click_forwards_to_on_cancel(self) -> None:
        calls = []

        async def on_cancel(interaction) -> None:
            calls.append(interaction)

        view = self._build_view(on_cancel=on_cancel)
        interaction = FakeInteraction()

        await view.children[2].callback(interaction)

        self.assertEqual(calls, [interaction])


if __name__ == "__main__":
    unittest.main()
