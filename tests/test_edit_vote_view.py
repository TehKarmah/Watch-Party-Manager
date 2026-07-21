"""Tests for FR-023's Discord UI components (edit_vote_view.py).

Mirrors test_start_vote_flow.py's StartVoteChoiceViewTests/CustomizeVoteModalTests
pattern: constructing each view/modal and confirming its buttons carry
stable custom_ids and forward clicks to the supplied callback. All actual
vote-editing logic lives in bot.py and is covered by test_edit_vote_command.py.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.edit_vote_view import (
    EDIT_VOTE_CONFIRMATION_TIMEOUT_SECONDS,
    EDIT_VOTE_VIEW_TIMEOUT_SECONDS,
    EditVoteConfirmationView,
    EditVoteEndTimeModal,
    EditVoteManagementView,
)


class EditVoteManagementViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction) -> None:
        pass

    def _view(self, on_change_end_time=None, on_end_now=None, on_cancel_vote=None) -> EditVoteManagementView:
        return EditVoteManagementView(
            on_change_end_time or self._noop, on_end_now or self._noop, on_cancel_vote or self._noop
        )

    async def test_has_three_buttons(self) -> None:
        view = self._view()
        self.assertEqual(len(view.children), 3)

    async def test_uses_the_expected_timeout(self) -> None:
        view = self._view()
        self.assertEqual(view.timeout, EDIT_VOTE_VIEW_TIMEOUT_SECONDS)

    async def test_buttons_have_stable_labels_and_custom_ids(self) -> None:
        view = self._view()
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Change End Time", "wpm_edit_vote_change_end_time"),
                ("End Now", "wpm_edit_vote_end_now"),
                ("Cancel Vote", "wpm_edit_vote_cancel_vote"),
            ],
        )

    async def test_change_end_time_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_change_end_time(interaction) -> None:
            calls.append("change_end_time")

        view = self._view(on_change_end_time=on_change_end_time)
        await view.children[0].callback(interaction=object())

        self.assertEqual(calls, ["change_end_time"])

    async def test_end_now_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_end_now(interaction) -> None:
            calls.append("end_now")

        view = self._view(on_end_now=on_end_now)
        await view.children[1].callback(interaction=object())

        self.assertEqual(calls, ["end_now"])

    async def test_cancel_vote_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_cancel_vote(interaction) -> None:
            calls.append("cancel_vote")

        view = self._view(on_cancel_vote=on_cancel_vote)
        await view.children[2].callback(interaction=object())

        self.assertEqual(calls, ["cancel_vote"])


class EditVoteEndTimeModalTests(unittest.IsolatedAsyncioTestCase):
    def _modal(self, on_submit=None, **kwargs) -> EditVoteEndTimeModal:
        async def noop(interaction, when_text) -> None:
            pass

        return EditVoteEndTimeModal(on_submit or noop, **kwargs)

    def test_has_a_single_required_field(self) -> None:
        modal = self._modal()
        self.assertEqual(len(modal.children), 1)
        self.assertTrue(modal.when_input.required)

    def test_pre_fills_the_current_value_when_given(self) -> None:
        modal = self._modal(current_value="2026-08-01 20:00")
        self.assertEqual(modal.when_input.default, "2026-08-01 20:00")

    async def test_submission_forwards_the_raw_text_to_the_callback(self) -> None:
        calls = []

        async def on_submit(interaction, when_text) -> None:
            calls.append(when_text)

        modal = self._modal(on_submit=on_submit)
        modal.when_input._value = "2027-01-01 12:00"
        await modal.on_submit(interaction=object())

        self.assertEqual(calls, ["2027-01-01 12:00"])


class EditVoteConfirmationViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction) -> None:
        pass

    def _view(self, confirm_label="End Now", on_confirm=None, on_abort=None) -> EditVoteConfirmationView:
        return EditVoteConfirmationView(
            confirm_label=confirm_label, on_confirm=on_confirm or self._noop, on_abort=on_abort or self._noop
        )

    async def test_has_two_buttons(self) -> None:
        view = self._view()
        self.assertEqual(len(view.children), 2)

    async def test_uses_the_expected_timeout(self) -> None:
        view = self._view()
        self.assertEqual(view.timeout, EDIT_VOTE_CONFIRMATION_TIMEOUT_SECONDS)

    async def test_confirm_button_uses_the_given_label_and_a_stable_custom_id(self) -> None:
        view = self._view(confirm_label="Cancel Vote")
        self.assertEqual(view.children[0].label, "Cancel Vote")
        self.assertEqual(view.children[0].custom_id, "wpm_edit_vote_confirm")

    async def test_abort_button_has_a_stable_label_and_custom_id(self) -> None:
        view = self._view()
        self.assertEqual(view.children[1].label, "Cancel")
        self.assertEqual(view.children[1].custom_id, "wpm_edit_vote_abort")

    async def test_confirm_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_confirm(interaction) -> None:
            calls.append("confirmed")

        view = self._view(on_confirm=on_confirm)
        await view.children[0].callback(interaction=object())

        self.assertEqual(calls, ["confirmed"])

    async def test_abort_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_abort(interaction) -> None:
            calls.append("aborted")

        view = self._view(on_abort=on_abort)
        await view.children[1].callback(interaction=object())

        self.assertEqual(calls, ["aborted"])

    async def test_reused_for_both_end_now_and_cancel_vote_confirmations(self) -> None:
        # Documents the deliberate design choice: one generic view class
        # covers both destructive confirmations rather than two near-
        # identical copies.
        end_now_view = self._view(confirm_label="End Now")
        cancel_view = self._view(confirm_label="Cancel Vote")

        self.assertIsInstance(end_now_view, EditVoteConfirmationView)
        self.assertIsInstance(cancel_view, EditVoteConfirmationView)
        self.assertNotEqual(end_now_view.children[0].label, cancel_view.children[0].label)


if __name__ == "__main__":
    unittest.main()
