"""Regression guard for Discord's TextInput label length limit (1-45 chars)
and for modal component types Discord actually accepts.

Discord rejects a modal outright (400 Bad Request, "Must be between 1 and
45 in length") if any discord.ui.TextInput label falls outside that
range. This was hit in production by VotingDefaultsModal's fourth field
("Candidate selection: random or balanced_random", 46 chars) -- fixed by
shortening the label to "Candidate selection" and moving the guidance
into its placeholder instead.

Separately: a discord.ui.Select embedded in a modal constructs without
error client-side (discord.py does not reject it), but Discord's API
rejects the resulting payload with a 400 "Invalid Form Body" ("Value of
field \"type\" must be one of (4,)") at submission time in a live
server. VotingDefaultsModal and ReminderDefaultsModal both briefly
regressed this way; fixed by collecting every fixed-choice value (
Candidate Selection Mode, Visibility) via Selects on a preceding view
instead, keeping every modal in the repository TextInput-only.

This file constructs every discord.ui.Modal in the repository and checks
every child's type and every TextInput's label length, so a similarly
invalid modal (a stray Select, or an out-of-range label) fails a fast,
focused test instead of a live Discord API call.
"""

import unittest

import discord

from watch_party_manager.edit_vote_view import CustomVoteEndTimeModal
from watch_party_manager.setup_wizard_view import (
    AdminChannelNameModal,
    BackupDefaultsModal,
    CreateDatabaseNameModal,
    CreateThreadNameModal,
    HomeChannelNameModal,
    ReminderDefaultsModal,
    SetupVotingDefaultsModal,
    VotingDefaultsModal,
)
from watch_party_manager.start_vote_view import CustomizeVoteModal
from watch_party_manager.suggestion_view import WatchedDateModal

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

    def test_voting_defaults_modal_has_exactly_two_labeled_text_fields(self) -> None:
        # Candidate Selection Mode and Visibility are both collected on
        # VotingDefaultsIntroView instead -- Discord's API rejects a
        # Select embedded in a modal at submission time (see module
        # docstring), so neither can live in this modal. Only candidate
        # count and duration remain as text fields here -- this modal is
        # guild-wide only; see SetupVotingDefaultsModal for the Setup
        # Wizard's own variant with a third, per-collection field.
        modal = VotingDefaultsModal(_noop)
        self.assertEqual(len(_text_input_labels(modal)), 2)

    def test_setup_voting_defaults_modal_labels_are_within_limit(self) -> None:
        self._assert_all_labels_within_limit(SetupVotingDefaultsModal(_noop))

    def test_setup_voting_defaults_modal_has_exactly_three_labeled_text_fields(self) -> None:
        modal = SetupVotingDefaultsModal(_noop)
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


class ModalComponentTypeTests(unittest.TestCase):
    """Every modal in the repository must contain TextInput components
    only -- Discord's API rejects any other component type inside a
    modal at submission time, even though discord.py allows constructing
    one (see module docstring).
    """

    def _all_modals(self):
        return [
            CreateDatabaseNameModal(_noop),
            AdminChannelNameModal(_noop),
            HomeChannelNameModal(_noop),
            CreateThreadNameModal(_noop),
            VotingDefaultsModal(_noop),
            SetupVotingDefaultsModal(_noop),
            ReminderDefaultsModal(_noop),
            BackupDefaultsModal(_noop),
            WatchedDateModal(1, _noop),
            CustomizeVoteModal(_noop),
            CustomVoteEndTimeModal(_noop),
        ]

    def test_every_modal_contains_only_text_inputs(self) -> None:
        for modal in self._all_modals():
            for child in modal.children:
                self.assertIsInstance(
                    child,
                    discord.ui.TextInput,
                    f"{type(modal).__name__} contains a {type(child).__name__}, "
                    "which Discord's API rejects inside a modal",
                )

    def test_voting_defaults_modal_serializes_with_only_type_4_components(self) -> None:
        self._assert_only_text_input_components(VotingDefaultsModal(_noop))

    def test_setup_voting_defaults_modal_serializes_with_only_type_4_components(self) -> None:
        self._assert_only_text_input_components(SetupVotingDefaultsModal(_noop))

    def test_reminder_defaults_modal_serializes_with_only_type_4_components(self) -> None:
        self._assert_only_text_input_components(ReminderDefaultsModal(_noop))

    def _assert_only_text_input_components(self, modal) -> None:
        # type 4 is Discord's TextInput component type -- "In
        # data.components.N.components.0: Value of field 'type' must be
        # one of (4,)" is the exact live-Discord error this guards
        # against, one nesting level below what to_components() returns
        # for a legacy (non-Label) modal field.
        for row in modal.to_components():
            for component in row.get("components", [row]):
                self.assertEqual(
                    component.get("type"),
                    4,
                    f"{type(modal).__name__} serializes a non-TextInput component: {component}",
                )


if __name__ == "__main__":
    unittest.main()
