"""Tests for FR-028's Discord UI components (setup_wizard_view.py).

Mirrors test_edit_vote_view.py's pattern: constructing each view/modal and
confirming its components carry stable custom_ids/labels and forward
selections/clicks/submissions to the supplied callback. All wizard logic
lives in services/setup_wizard_service.py and bot.py's wiring around it.
"""

import unittest

from watch_party_manager.domain.guild_configuration import JoinMode
from watch_party_manager.setup_wizard_view import (
    SETUP_WIZARD_STEP_TIMEOUT_SECONDS,
    AdminChannelStepView,
    BackupDefaultsModal,
    CreateDatabaseChannelSelectView,
    CreateDatabaseNameModal,
    ExistingDatabaseSelectView,
    ModalStepIntroView,
    ReminderDefaultsModal,
    ReviewStepView,
    SetupCancelButton,
    SetupWizardResumeView,
    SuggestionDatabaseChoiceView,
    VotingDefaultsModal,
    WashCrewRoleStepView,
    WatchDestinationStepView,
    WatchPartyRoleStepView,
)


async def _noop(*args) -> None:
    pass


class SetupCancelButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_stable_label_and_custom_id(self) -> None:
        button = SetupCancelButton(_noop)
        self.assertEqual(button.label, "Cancel Setup")
        self.assertEqual(button.custom_id, "wpm_setup_cancel")

    async def test_click_forwards_to_callback(self) -> None:
        calls = []

        async def on_cancel(interaction) -> None:
            calls.append(interaction)

        button = SetupCancelButton(on_cancel)
        await button.callback(interaction="fake-interaction")
        self.assertEqual(calls, ["fake-interaction"])


class SetupWizardResumeViewTests(unittest.IsolatedAsyncioTestCase):
    def _view(self, on_continue=None, on_review=None, on_restart=None) -> SetupWizardResumeView:
        return SetupWizardResumeView(on_continue or _noop, on_review or _noop, on_restart or _noop)

    async def test_has_three_buttons_with_the_expected_timeout(self) -> None:
        view = self._view()
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.timeout, SETUP_WIZARD_STEP_TIMEOUT_SECONDS)

    async def test_buttons_have_stable_labels_and_custom_ids(self) -> None:
        view = self._view()
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Continue Setup", "wpm_setup_resume_continue"),
                ("Review Progress", "wpm_setup_resume_review"),
                ("Restart Setup", "wpm_setup_resume_restart"),
            ],
        )

    async def test_continue_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_continue(interaction) -> None:
            calls.append("continue")

        view = self._view(on_continue=on_continue)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["continue"])

    async def test_restart_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_restart(interaction) -> None:
            calls.append("restart")

        view = self._view(on_restart=on_restart)
        await view.children[2].callback(interaction=object())
        self.assertEqual(calls, ["restart"])


class WashCrewRoleStepViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_a_role_select_and_a_cancel_button(self) -> None:
        view = WashCrewRoleStepView(_noop, _noop)
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.children[0].custom_id, "wpm_setup_wash_crew_role_select")
        self.assertEqual(view.children[0].min_values, 1)
        self.assertEqual(view.children[0].max_values, 1)


class WatchPartyRoleStepViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_role_select_join_mode_select_confirm_and_cancel(self) -> None:
        view = WatchPartyRoleStepView(_noop, _noop)
        self.assertEqual(len(view.children), 4)
        self.assertEqual(view.role_select.min_values, 0)

    async def test_confirm_reads_selected_role_and_join_mode(self) -> None:
        calls = []

        async def on_confirm(interaction, role_id, join_mode) -> None:
            calls.append((role_id, join_mode))

        view = WatchPartyRoleStepView(on_confirm, _noop)

        class FakeRoleValue:
            id = 222

        view.role_select._values = [FakeRoleValue()]
        view.join_mode_select._values = [JoinMode.APPROVAL.value]

        await view._handle_confirm(interaction=object())
        self.assertEqual(calls, [(222, JoinMode.APPROVAL)])

    async def test_confirm_defaults_join_mode_to_self_service_when_untouched(self) -> None:
        calls = []

        async def on_confirm(interaction, role_id, join_mode) -> None:
            calls.append((role_id, join_mode))

        view = WatchPartyRoleStepView(on_confirm, _noop)
        await view._handle_confirm(interaction=object())
        self.assertEqual(calls, [(None, JoinMode.SELF_SERVICE)])


class SuggestionDatabaseChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_select_existing_create_new_and_cancel_buttons(self) -> None:
        view = SuggestionDatabaseChoiceView(_noop, _noop, _noop)
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Select Existing", "wpm_setup_database_select_existing"),
                ("Create New", "wpm_setup_database_create_new"),
                ("Cancel Setup", "wpm_setup_cancel"),
            ],
        )


class ExistingDatabaseSelectViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_one_option_per_database(self) -> None:
        view = ExistingDatabaseSelectView([(1, "Movies"), (2, "TV Shows")], _noop, _noop)
        select = view.children[0]
        self.assertEqual([option.value for option in select.options], ["1", "2"])
        self.assertEqual([option.label for option in select.options], ["Movies", "TV Shows"])

    async def test_selection_forwards_the_chosen_database_id(self) -> None:
        calls = []

        async def on_select(interaction, database_id) -> None:
            calls.append(database_id)

        view = ExistingDatabaseSelectView([(5, "Movies")], on_select, _noop)
        select = view.children[0]
        select._values = ["5"]
        await select.callback(interaction=object())
        self.assertEqual(calls, [5])


class CreateDatabaseNameModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_submission_forwards_the_entered_name(self) -> None:
        calls = []

        async def on_submit(interaction, name) -> None:
            calls.append(name)

        modal = CreateDatabaseNameModal(on_submit)
        modal.name_input._value = "Movies"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, ["Movies"])


class CreateDatabaseChannelSelectViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_a_channel_select_and_a_cancel_button(self) -> None:
        view = CreateDatabaseChannelSelectView(_noop, _noop)
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.children[0].custom_id, "wpm_setup_database_channel_select")


class AdminChannelStepViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_channel_select_skip_and_cancel(self) -> None:
        view = AdminChannelStepView(_noop, _noop, _noop)
        self.assertEqual(
            [getattr(child, "label", None) or getattr(child, "custom_id", None) for child in view.children],
            ["wpm_setup_admin_channel_select", "Skip for Now", "Cancel Setup"],
        )

    async def test_skip_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_skip(interaction) -> None:
            calls.append("skip")

        view = AdminChannelStepView(_noop, on_skip, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["skip"])


class WatchDestinationStepViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_channel_select_skip_and_cancel(self) -> None:
        view = WatchDestinationStepView(_noop, _noop, _noop)
        self.assertEqual(
            [getattr(child, "label", None) or getattr(child, "custom_id", None) for child in view.children],
            ["wpm_setup_watch_destination_channel_select", "Skip for Now", "Cancel Setup"],
        )

    async def test_skip_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_skip(interaction) -> None:
            calls.append("skip")

        view = WatchDestinationStepView(_noop, on_skip, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["skip"])


class ModalStepIntroViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_a_configure_button_with_the_given_label_and_id(self) -> None:
        view = ModalStepIntroView(_noop, _noop, button_label="Set Voting Defaults", custom_id="wpm_test_configure")
        self.assertEqual(view.children[0].label, "Set Voting Defaults")
        self.assertEqual(view.children[0].custom_id, "wpm_test_configure")

    async def test_configure_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_configure(interaction) -> None:
            calls.append("configure")

        view = ModalStepIntroView(on_configure, _noop, button_label="Go", custom_id="wpm_test_configure")
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["configure"])


class VotingDefaultsModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_four_fields_with_expected_defaults(self) -> None:
        modal = VotingDefaultsModal(_noop)
        self.assertEqual(len(modal.children), 4)
        self.assertEqual(modal.candidate_count_input.default, "3")
        self.assertEqual(modal.duration_days_input.default, "7")
        self.assertEqual(modal.visibility_input.default, "blind")
        self.assertEqual(modal.candidate_selection_input.default, "balanced_random")

    async def test_submission_forwards_all_four_values(self) -> None:
        calls = []

        async def on_submit(interaction, candidate_count, duration_days, visibility, candidate_selection) -> None:
            calls.append((candidate_count, duration_days, visibility, candidate_selection))

        modal = VotingDefaultsModal(on_submit)
        modal.candidate_count_input._value = "4"
        modal.duration_days_input._value = "10"
        modal.visibility_input._value = "visible"
        modal.candidate_selection_input._value = "random"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, [("4", "10", "visible", "random")])


class ReminderDefaultsModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_two_fields_with_expected_defaults(self) -> None:
        modal = ReminderDefaultsModal(_noop)
        self.assertEqual(len(modal.children), 2)
        self.assertEqual(modal.enabled_input.default, "yes")
        self.assertEqual(modal.hours_input.default, "24")

    async def test_submission_forwards_both_values(self) -> None:
        calls = []

        async def on_submit(interaction, enabled, hours) -> None:
            calls.append((enabled, hours))

        modal = ReminderDefaultsModal(on_submit)
        modal.enabled_input._value = "no"
        modal.hours_input._value = "48"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, [("no", "48")])


class BackupDefaultsModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_two_fields_with_expected_defaults(self) -> None:
        modal = BackupDefaultsModal(_noop)
        self.assertEqual(len(modal.children), 2)
        self.assertEqual(modal.interval_input.default, "1")
        self.assertEqual(modal.retention_input.default, "30")

    async def test_submission_forwards_both_values(self) -> None:
        calls = []

        async def on_submit(interaction, interval, retention) -> None:
            calls.append((interval, retention))

        modal = BackupDefaultsModal(on_submit)
        modal.interval_input._value = "2"
        modal.retention_input._value = "15"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, [("2", "15")])


class ReviewStepViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_save_edit_section_and_cancel(self) -> None:
        view = ReviewStepView([("wash_crew_role", "WASH Crew Role")], _noop, _noop, _noop)
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.children[0].label, "Save")
        self.assertEqual(view.children[0].custom_id, "wpm_setup_review_save")

    async def test_save_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_save(interaction) -> None:
            calls.append("save")

        view = ReviewStepView([("wash_crew_role", "WASH Crew Role")], on_save, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["save"])

    async def test_edit_section_select_forwards_the_chosen_step_value(self) -> None:
        calls = []

        async def on_edit_section(interaction, step_value) -> None:
            calls.append(step_value)

        view = ReviewStepView(
            [("wash_crew_role", "WASH Crew Role"), ("review", "Review")], _noop, on_edit_section, _noop
        )
        select = view.children[1]
        select._values = ["review"]
        await select.callback(interaction=object())
        self.assertEqual(calls, ["review"])


if __name__ == "__main__":
    unittest.main()
