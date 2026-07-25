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
    DURATION_DELTA_QUICK_PICKS,
    EDIT_VOTE_CONFIRMATION_TIMEOUT_SECONDS,
    EDIT_VOTE_VIEW_TIMEOUT_SECONDS,
    CustomDurationModal,
    CustomVoteEndTimeModal,
    DurationDeltaChoiceView,
    EditVoteConfirmationView,
    EditVoteManagementView,
    VoteEndTimeMenuView,
)


class EditVoteManagementViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction) -> None:
        pass

    def _view(self, on_change_end_time=None, on_cancel_vote=None) -> EditVoteManagementView:
        return EditVoteManagementView(on_change_end_time or self._noop, on_cancel_vote or self._noop)

    async def test_has_two_buttons(self) -> None:
        view = self._view()
        self.assertEqual(len(view.children), 2)

    async def test_uses_the_expected_timeout(self) -> None:
        view = self._view()
        self.assertEqual(view.timeout, EDIT_VOTE_VIEW_TIMEOUT_SECONDS)

    async def test_buttons_have_stable_labels_and_custom_ids(self) -> None:
        view = self._view()
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Change End Time", "wpm_edit_vote_change_end_time"),
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

    async def test_cancel_vote_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_cancel_vote(interaction) -> None:
            calls.append("cancel_vote")

        view = self._view(on_cancel_vote=on_cancel_vote)
        await view.children[1].callback(interaction=object())

        self.assertEqual(calls, ["cancel_vote"])


class VoteEndTimeMenuViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop_end_now(self, interaction) -> None:
        pass

    async def _noop_shorten(self, interaction) -> None:
        pass

    async def _noop_extend(self, interaction) -> None:
        pass

    async def _noop_set_exact(self, interaction) -> None:
        pass

    def _view(self, on_end_now=None, on_shorten=None, on_extend=None, on_set_exact=None) -> VoteEndTimeMenuView:
        return VoteEndTimeMenuView(
            on_end_now or self._noop_end_now,
            on_shorten or self._noop_shorten,
            on_extend or self._noop_extend,
            on_set_exact or self._noop_set_exact,
        )

    def test_has_four_buttons(self) -> None:
        view = self._view()
        self.assertEqual(len(view.children), 4)

    def test_uses_the_expected_timeout(self) -> None:
        view = self._view()
        self.assertEqual(view.timeout, EDIT_VOTE_VIEW_TIMEOUT_SECONDS)

    def test_button_labels_match_the_specified_options_in_order(self) -> None:
        view = self._view()
        labels = [button.label for button in view.children]
        self.assertEqual(labels, ["End Now", "Shorten Vote", "Extend Vote", "Set Exact End Time"])

    async def test_end_now_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_end_now(interaction) -> None:
            calls.append("end_now")

        view = self._view(on_end_now=on_end_now)
        await view.children[0].callback(interaction=object())

        self.assertEqual(calls, ["end_now"])

    async def test_shorten_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_shorten(interaction) -> None:
            calls.append("shorten")

        view = self._view(on_shorten=on_shorten)
        await view.children[1].callback(interaction=object())

        self.assertEqual(calls, ["shorten"])

    async def test_extend_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_extend(interaction) -> None:
            calls.append("extend")

        view = self._view(on_extend=on_extend)
        await view.children[2].callback(interaction=object())

        self.assertEqual(calls, ["extend"])

    async def test_set_exact_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_set_exact(interaction) -> None:
            calls.append("set_exact")

        view = self._view(on_set_exact=on_set_exact)
        await view.children[3].callback(interaction=object())

        self.assertEqual(calls, ["set_exact"])


class DurationDeltaChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop_pick(self, interaction, minutes) -> None:
        pass

    async def _noop_custom(self, interaction) -> None:
        pass

    def _view(self, on_quick_pick=None, on_choose_custom=None) -> DurationDeltaChoiceView:
        return DurationDeltaChoiceView(
            on_quick_pick or self._noop_pick,
            on_choose_custom or self._noop_custom,
            custom_id_prefix="wpm_edit_vote_test_pick",
        )

    def test_has_three_buttons(self) -> None:
        view = self._view()
        self.assertEqual(len(view.children), 3)

    def test_uses_the_expected_timeout(self) -> None:
        view = self._view()
        self.assertEqual(view.timeout, EDIT_VOTE_VIEW_TIMEOUT_SECONDS)

    def test_button_labels_match_the_specified_options_in_order(self) -> None:
        view = self._view()
        labels = [button.label for button in view.children]
        self.assertEqual(labels, ["1 Hour", "1 Day", "Custom..."])

    async def test_each_quick_pick_matches_its_declared_minutes(self) -> None:
        for index, (_, minutes) in enumerate(DURATION_DELTA_QUICK_PICKS):
            calls = []

            async def on_quick_pick(interaction, picked_minutes, calls=calls) -> None:
                calls.append(picked_minutes)

            view = self._view(on_quick_pick=on_quick_pick)
            await view.children[index].callback(interaction=object())

            self.assertEqual(calls, [minutes])

    async def test_choose_custom_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_choose_custom(interaction) -> None:
            calls.append("custom")

        view = self._view(on_choose_custom=on_choose_custom)
        await view.children[2].callback(interaction=object())

        self.assertEqual(calls, ["custom"])

    def test_custom_id_prefix_distinguishes_shorten_from_extend(self) -> None:
        shorten_view = DurationDeltaChoiceView(
            self._noop_pick, self._noop_custom, custom_id_prefix="wpm_edit_vote_shorten_pick"
        )
        extend_view = DurationDeltaChoiceView(
            self._noop_pick, self._noop_custom, custom_id_prefix="wpm_edit_vote_extend_pick"
        )
        shorten_ids = {child.custom_id for child in shorten_view.children if child.custom_id}
        extend_ids = {child.custom_id for child in extend_view.children if child.custom_id}
        self.assertTrue(shorten_ids & {"wpm_edit_vote_shorten_pick_0", "wpm_edit_vote_shorten_pick_1"})
        self.assertTrue(extend_ids & {"wpm_edit_vote_extend_pick_0", "wpm_edit_vote_extend_pick_1"})
        self.assertFalse(shorten_ids & extend_ids)


class CustomDurationModalTests(unittest.IsolatedAsyncioTestCase):
    def _modal(self, on_submit=None, *, title="Shorten Vote") -> CustomDurationModal:
        async def noop(interaction, duration_text) -> None:
            pass

        return CustomDurationModal(on_submit or noop, title=title)

    def test_has_one_required_field(self) -> None:
        modal = self._modal()
        self.assertEqual(len(modal.children), 1)
        self.assertTrue(modal.duration_input.required)

    def test_field_uses_the_expected_label_and_placeholder(self) -> None:
        modal = self._modal()
        self.assertEqual(modal.duration_input.label, "Duration")
        self.assertEqual(modal.duration_input.placeholder, "e.g. 10m, 1h, 1d, 1w")

    def test_title_names_the_action_being_customized(self) -> None:
        shorten_modal = self._modal(title="Shorten Vote")
        extend_modal = self._modal(title="Extend Vote")
        self.assertEqual(shorten_modal.title, "Shorten Vote")
        self.assertEqual(extend_modal.title, "Extend Vote")

    async def test_submission_forwards_the_raw_text_to_the_callback(self) -> None:
        calls = []

        async def on_submit(interaction, duration_text) -> None:
            calls.append(duration_text)

        modal = self._modal(on_submit=on_submit)
        modal.duration_input._value = "10m"
        await modal.on_submit(interaction=object())

        self.assertEqual(calls, ["10m"])


class CustomVoteEndTimeModalTests(unittest.IsolatedAsyncioTestCase):
    def _modal(self, on_submit=None) -> CustomVoteEndTimeModal:
        async def noop(interaction, timestamp_text) -> None:
            pass

        return CustomVoteEndTimeModal(on_submit or noop)

    def test_has_one_required_field(self) -> None:
        modal = self._modal()
        self.assertEqual(len(modal.children), 1)
        self.assertTrue(modal.timestamp_input.required)

    def test_field_uses_the_expected_label_and_placeholder(self) -> None:
        modal = self._modal()
        self.assertEqual(modal.timestamp_input.label, "Discord Timestamp")
        self.assertEqual(modal.timestamp_input.placeholder, "<t:1785639600:F>")

    async def test_submission_forwards_the_raw_text_to_the_callback(self) -> None:
        calls = []

        async def on_submit(interaction, timestamp_text) -> None:
            calls.append(timestamp_text)

        modal = self._modal(on_submit=on_submit)
        modal.timestamp_input._value = "<t:1785639600:F>"
        await modal.on_submit(interaction=object())

        self.assertEqual(calls, ["<t:1785639600:F>"])


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
