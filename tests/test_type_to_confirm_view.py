"""Tests for FR-032C's shared "type X to confirm" Discord UI."""

from __future__ import annotations

import unittest

from watch_party_manager.type_to_confirm_view import (
    DestructiveConfirmationView,
    TypeToConfirmModal,
)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None
        self.sent_modal = None

    async def send_message(self, content, ephemeral=False) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral

    async def send_modal(self, modal) -> None:
        self.sent_modal = modal


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponse()


class TypeToConfirmModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_match_forwards_to_on_confirm(self) -> None:
        calls = []

        async def on_confirm(interaction) -> None:
            calls.append(interaction)

        modal = TypeToConfirmModal(title="Reset Database", required_text="RESET", on_confirm=on_confirm)
        modal.confirmation_input._value = "RESET"
        interaction = FakeInteraction()

        await modal.on_submit(interaction)

        self.assertEqual(calls, [interaction])
        self.assertIsNone(interaction.response.sent_message)

    async def test_mismatch_does_not_confirm_and_reports_no_changes(self) -> None:
        calls = []

        async def on_confirm(interaction) -> None:
            calls.append(interaction)

        modal = TypeToConfirmModal(title="Reset Database", required_text="RESET", on_confirm=on_confirm)
        modal.confirmation_input._value = "reset"  # wrong case
        interaction = FakeInteraction()

        await modal.on_submit(interaction)

        self.assertEqual(calls, [])
        self.assertIn("No changes were made", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_partial_or_extra_text_is_rejected(self) -> None:
        calls = []

        async def on_confirm(interaction) -> None:
            calls.append(interaction)

        modal = TypeToConfirmModal(title="Replace Data", required_text="REPLACE", on_confirm=on_confirm)
        modal.confirmation_input._value = "REPLACE NOW"
        interaction = FakeInteraction()

        await modal.on_submit(interaction)

        self.assertEqual(calls, [])

    async def test_label_includes_the_required_phrase(self) -> None:
        modal = TypeToConfirmModal(title="Reset Database", required_text="RESET", on_confirm=lambda i: None)

        self.assertIn("RESET", modal.confirmation_input.label)


class DestructiveConfirmationViewTests(unittest.IsolatedAsyncioTestCase):
    def _build_view(self, on_confirm=None, on_cancel=None) -> DestructiveConfirmationView:
        async def noop(interaction) -> None:
            pass

        return DestructiveConfirmationView(
            button_label="Reset",
            required_text="RESET",
            modal_title="Reset Database",
            custom_id_prefix="database_reset",
            on_confirm=on_confirm or noop,
            on_cancel=on_cancel or noop,
        )

    async def test_has_two_buttons(self) -> None:
        view = self._build_view()
        self.assertEqual(len(view.children), 2)

    async def test_button_labels(self) -> None:
        view = self._build_view()
        self.assertEqual(view.children[0].label, "Reset")
        self.assertEqual(view.children[1].label, "Cancel")

    async def test_custom_ids_are_stable_and_distinct(self) -> None:
        view = self._build_view()
        self.assertEqual(view.children[0].custom_id, "wpm_database_reset_open")
        self.assertEqual(view.children[1].custom_id, "wpm_database_reset_cancel")

    async def test_clicking_the_primary_button_opens_the_modal(self) -> None:
        view = self._build_view()
        interaction = FakeInteraction()

        await view.children[0].callback(interaction)

        self.assertIsInstance(interaction.response.sent_modal, TypeToConfirmModal)

    async def test_clicking_cancel_forwards_to_on_cancel(self) -> None:
        calls = []

        async def on_cancel(interaction) -> None:
            calls.append(interaction)

        view = self._build_view(on_cancel=on_cancel)
        interaction = FakeInteraction()

        await view.children[1].callback(interaction)

        self.assertEqual(calls, [interaction])

    async def test_different_prefixes_produce_different_custom_ids(self) -> None:
        async def noop(interaction) -> None:
            pass

        first = DestructiveConfirmationView(
            button_label="Reset",
            required_text="RESET",
            modal_title="Reset Database",
            custom_id_prefix="database_reset",
            on_confirm=noop,
            on_cancel=noop,
        )
        second = DestructiveConfirmationView(
            button_label="Reset",
            required_text="RESET",
            modal_title="Factory Reset",
            custom_id_prefix="factory_reset",
            on_confirm=noop,
            on_cancel=noop,
        )

        self.assertNotEqual(first.children[0].custom_id, second.children[0].custom_id)


if __name__ == "__main__":
    unittest.main()
