"""Regression guard for Discord's TextInput label length limit (1-45 chars).

Discord rejects a modal outright (400 Bad Request, "Must be between 1 and
45 in length") if any discord.ui.TextInput label falls outside that
range. This was hit in production by VotingDefaultsModal's fourth field
("Candidate selection: random or balanced_random", 46 chars) -- fixed by
shortening the label to "Candidate selection" and moving the guidance
into its placeholder instead.

This file constructs every discord.ui.Modal in the repository and checks
every TextInput child's label length, so a similarly-long label added to
any modal (now, or in the future) fails a fast, focused test instead of
a live Discord API call.
"""

import unittest

from watch_party_manager.edit_vote_view import EditVoteEndTimeModal
from watch_party_manager.setup_wizard_view import (
    BackupDefaultsModal,
    CreateDatabaseNameModal,
    ReminderDefaultsModal,
    VotingDefaultsModal,
)
from watch_party_manager.start_vote_view import CustomizeVoteModal

DISCORD_TEXT_INPUT_LABEL_MIN_LENGTH = 1
DISCORD_TEXT_INPUT_LABEL_MAX_LENGTH = 45


async def _noop(*args) -> None:
    pass


def _text_input_labels(modal) -> list[str]:
    return [child.label for child in modal.children if hasattr(child, "label")]


class ModalTextInputLabelLengthTests(unittest.TestCase):
    """Every modal's TextInput labels must fit Discord's 1-45 char limit."""

    def _assert_all_labels_within_limit(self, modal) -> None:
        labels = _text_input_labels(modal)
        self.assertTrue(labels, "expected at least one TextInput on this modal")
        for label in labels:
            self.assertGreaterEqual(
                len(label), DISCORD_TEXT_INPUT_LABEL_MIN_LENGTH, f"label {label!r} is shorter than Discord allows"
            )
            self.assertLessEqual(
                len(label), DISCORD_TEXT_INPUT_LABEL_MAX_LENGTH, f"label {label!r} ({len(label)} chars) exceeds Discord's 45-char limit"
            )

    def test_voting_defaults_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(VotingDefaultsModal(_noop))

    def test_voting_defaults_modal_has_exactly_four_labeled_fields(self) -> None:
        modal = VotingDefaultsModal(_noop)
        self.assertEqual(len(_text_input_labels(modal)), 4)

    def test_voting_defaults_modal_fourth_field_label_was_shortened(self) -> None:
        # The exact field that triggered the 400 Bad Request in production.
        modal = VotingDefaultsModal(_noop)
        self.assertEqual(modal.candidate_selection_input.label, "Candidate selection")
        self.assertLessEqual(len(modal.candidate_selection_input.label), DISCORD_TEXT_INPUT_LABEL_MAX_LENGTH)

    def test_voting_defaults_modal_preserves_guidance_in_the_placeholder(self) -> None:
        # The wording removed from the label must still reach the user.
        modal = VotingDefaultsModal(_noop)
        self.assertIn("rotation_pool", modal.candidate_selection_input.placeholder or "")
        self.assertIn("soft_rotation", modal.candidate_selection_input.placeholder or "")
        self.assertIn("infinite_pool", modal.candidate_selection_input.placeholder or "")

    def test_reminder_defaults_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(ReminderDefaultsModal(_noop))

    def test_backup_defaults_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(BackupDefaultsModal(_noop))

    def test_create_database_name_modal_label_is_within_limit(self) -> None:
        self._assert_all_labels_within_limit(CreateDatabaseNameModal(_noop))

    def test_customize_vote_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(CustomizeVoteModal(_noop))

    def test_edit_vote_end_time_modal_label_is_within_limit(self) -> None:
        self._assert_all_labels_within_limit(EditVoteEndTimeModal(_noop))


if __name__ == "__main__":
    unittest.main()
