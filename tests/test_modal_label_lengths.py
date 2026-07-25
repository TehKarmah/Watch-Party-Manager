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

from watch_party_manager.edit_vote_view import CustomVoteEndTimeModal
from watch_party_manager.setup_wizard_view import (
    BackupDefaultsModal,
    CreateDatabaseNameModal,
    CreateThreadNameModal,
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

    def test_voting_defaults_modal_has_exactly_three_labeled_fields(self) -> None:
        # Candidate selection moved to a Discord Select (see
        # CandidateSelectionSelectComponent), which cannot live inside a
        # modal -- only candidate count, duration, and visibility remain
        # here as text fields.
        modal = VotingDefaultsModal(_noop)
        self.assertEqual(len(_text_input_labels(modal)), 3)

    def test_reminder_defaults_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(ReminderDefaultsModal(_noop))

    def test_backup_defaults_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(BackupDefaultsModal(_noop))

    def test_create_database_name_modal_label_is_within_limit(self) -> None:
        self._assert_all_labels_within_limit(CreateDatabaseNameModal(_noop))

    def test_customize_vote_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(CustomizeVoteModal(_noop))

    def test_custom_vote_end_time_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(CustomVoteEndTimeModal(_noop))

    def test_create_thread_name_modal_label_is_within_limit(self) -> None:
        self._assert_all_labels_within_limit(CreateThreadNameModal(_noop))


if __name__ == "__main__":
    unittest.main()
