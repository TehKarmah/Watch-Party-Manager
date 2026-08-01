"""Tests for FR-028's Discord UI components (setup_wizard_view.py).

Mirrors test_edit_vote_view.py's pattern: constructing each view/modal and
confirming its components carry stable custom_ids/labels and forward
selections/clicks/submissions to the supplied callback. All wizard logic
lives in services/setup_wizard_service.py and bot.py's wiring around it.
"""

import unittest

import discord

from watch_party_manager.domain.guild_configuration import GuildVoteVisibility, JoinMode
from watch_party_manager.domain.suggestion_database_configuration import (
    CandidateSelectionMode,
    NOMINEE_SELECTION_MODE_ORDER,
)
from watch_party_manager.services.discord_ui_limits import find_oversized_view_component_fields
from watch_party_manager.setup_wizard_view import (
    SETUP_WIZARD_STEP_TIMEOUT_SECONDS,
    AdminChannelStepView,
    BackupDefaultsModal,
    BeginSetupButton,
    CandidateSelectionSelectComponent,
    CreateDatabaseNameModal,
    CreateThreadNameModal,
    ExistingChannelSelectView,
    ExistingDatabaseSelectView,
    HomeChannelChoiceView,
    HomeChannelNameModal,
    ModalStepIntroView,
    ReminderDefaultsChoiceView,
    ReminderDefaultsModal,
    ReviewStepView,
    SetupBackButton,
    SetupCancelButton,
    SetupPreparationView,
    SetupSaveForLaterButton,
    SetupVotingDefaultsModal,
    SetupWizardResumeView,
    SuggestionDatabaseChoiceView,
    VisibilitySelectComponent,
    VotingDefaultsIntroView,
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
    async def test_has_a_role_select_save_for_later_and_cancel_but_no_back(self) -> None:
        # The first step never shows a Back button (Section 1 requirement).
        view = WashCrewRoleStepView(_noop, _noop, _noop)
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.children[0].custom_id, "wpm_setup_wash_crew_role_select")
        self.assertEqual(view.children[0].min_values, 1)
        self.assertEqual(view.children[0].max_values, 1)
        self.assertFalse(any(isinstance(child, SetupBackButton) for child in view.children))
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))
        self.assertIsInstance(view.children[-1], SetupCancelButton)

    async def test_save_for_later_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_save_for_later(interaction) -> None:
            calls.append("saved")

        view = WashCrewRoleStepView(_noop, on_save_for_later, _noop)
        save_for_later_button = next(c for c in view.children if isinstance(c, SetupSaveForLaterButton))
        await save_for_later_button.callback(interaction=object())
        self.assertEqual(calls, ["saved"])


class WatchPartyRoleStepViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_role_select_join_mode_select_confirm_back_save_and_cancel(self) -> None:
        view = WatchPartyRoleStepView(_noop, _noop, _noop, _noop)
        self.assertEqual(len(view.children), 6)
        self.assertEqual(view.role_select.min_values, 0)
        self.assertIsInstance(view.children[-1], SetupCancelButton)
        self.assertTrue(any(isinstance(child, SetupBackButton) for child in view.children))
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))

    async def test_confirm_reads_selected_role_and_join_mode(self) -> None:
        calls = []

        async def on_confirm(interaction, role_id, join_mode) -> None:
            calls.append((role_id, join_mode))

        view = WatchPartyRoleStepView(on_confirm, _noop, _noop, _noop)

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

        view = WatchPartyRoleStepView(on_confirm, _noop, _noop, _noop)
        await view._handle_confirm(interaction=object())
        self.assertEqual(calls, [(None, JoinMode.SELF_SERVICE)])

    async def test_back_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append("back")

        view = WatchPartyRoleStepView(_noop, on_back, _noop, _noop)
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        await back_button.callback(interaction=object())
        self.assertEqual(calls, ["back"])


class SuggestionDatabaseChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_create_new_select_existing_back_save_and_cancel_buttons(self) -> None:
        # Release Polish (Batch): Create New is the recommended, default
        # action -- it appears first, ahead of Select Existing.
        view = SuggestionDatabaseChoiceView(_noop, _noop, _noop, _noop, _noop)
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Create New", "wpm_setup_database_create_new"),
                ("Select Existing", "wpm_setup_database_select_existing"),
                ("Back", "wpm_setup_back"),
                ("Save & Finish Later", "wpm_setup_save_for_later"),
                ("Cancel Setup", "wpm_setup_cancel"),
            ],
        )

    async def test_create_new_uses_the_primary_style_and_select_existing_uses_secondary(self) -> None:
        view = SuggestionDatabaseChoiceView(_noop, _noop, _noop, _noop, _noop)
        styles = {button.custom_id: button.style for button in view.children}
        self.assertEqual(styles["wpm_setup_database_create_new"], discord.ButtonStyle.primary)
        self.assertEqual(styles["wpm_setup_database_select_existing"], discord.ButtonStyle.secondary)

    async def test_cancel_setup_remains_the_danger_style(self) -> None:
        view = SuggestionDatabaseChoiceView(_noop, _noop, _noop, _noop, _noop)
        cancel_button = next(button for button in view.children if button.custom_id == "wpm_setup_cancel")
        self.assertEqual(cancel_button.style, discord.ButtonStyle.danger)


class ExistingDatabaseSelectViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_one_option_per_database(self) -> None:
        view = ExistingDatabaseSelectView([(1, "Movies"), (2, "TV Shows")], _noop, _noop, _noop, _noop)
        select = view.children[0]
        self.assertEqual([option.value for option in select.options], ["1", "2"])
        self.assertEqual([option.label for option in select.options], ["Movies", "TV Shows"])

    async def test_selection_forwards_the_chosen_database_id(self) -> None:
        calls = []

        async def on_select(interaction, database_id) -> None:
            calls.append(database_id)

        view = ExistingDatabaseSelectView([(5, "Movies")], on_select, _noop, _noop, _noop)
        select = view.children[0]
        select._values = ["5"]
        await select.callback(interaction=object())
        self.assertEqual(calls, [5])

    async def test_has_back_save_for_later_and_cancel(self) -> None:
        # Live-testing fix: this nested sub-screen previously offered
        # Cancel Setup only.
        view = ExistingDatabaseSelectView([(1, "Movies")], _noop, _noop, _noop, _noop)
        labels = [getattr(child, "label", None) for child in view.children]
        self.assertIn("Back", labels)
        self.assertIn("Save & Finish Later", labels)
        self.assertIn("Cancel Setup", labels)

    async def test_back_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append("back")

        view = ExistingDatabaseSelectView([(1, "Movies")], _noop, on_back, _noop, _noop)
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        await back_button.callback(interaction=object())
        self.assertEqual(calls, ["back"])


class CreateDatabaseNameModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_submission_forwards_the_entered_name(self) -> None:
        calls = []

        async def on_submit(interaction, name) -> None:
            calls.append(name)

        modal = CreateDatabaseNameModal(on_submit)
        modal.name_input._value = "Movies"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, ["Movies"])


_SAMPLE_DESTINATION_OPTIONS = [discord.SelectOption(label="general", value="1")]


class AdminChannelStepViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_channel_select_create_new_skip_back_save_and_cancel(self) -> None:
        view = AdminChannelStepView(_SAMPLE_DESTINATION_OPTIONS, _noop, _noop, _noop, _noop, _noop, _noop)
        self.assertEqual(
            [getattr(child, "label", None) or getattr(child, "custom_id", None) for child in view.children],
            [
                "wpm_setup_admin_channel_select",
                "Create New Channel",
                "Skip for Now",
                "Back",
                "Save & Finish Later",
                "Cancel Setup",
            ],
        )

    async def test_create_new_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_create_new(interaction) -> None:
            calls.append("create_new")

        view = AdminChannelStepView(_SAMPLE_DESTINATION_OPTIONS, _noop, on_create_new, _noop, _noop, _noop, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["create_new"])

    async def test_skip_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_skip(interaction) -> None:
            calls.append("skip")

        view = AdminChannelStepView(_SAMPLE_DESTINATION_OPTIONS, _noop, _noop, on_skip, _noop, _noop, _noop)
        await view.children[2].callback(interaction=object())
        self.assertEqual(calls, ["skip"])

    async def test_back_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append("back")

        view = AdminChannelStepView(_SAMPLE_DESTINATION_OPTIONS, _noop, _noop, _noop, on_back, _noop, _noop)
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        await back_button.callback(interaction=object())
        self.assertEqual(calls, ["back"])

    async def test_select_options_reflect_the_supplied_options(self) -> None:
        # Live-testing fix: options are built fresh by the caller on
        # every render (see bot.py's build_channel_destination_options),
        # not auto-populated by Discord -- confirm the view actually uses
        # whatever list it's given.
        options = [discord.SelectOption(label="watch-party › archive", value="42", default=True)]
        view = AdminChannelStepView(options, _noop, _noop, _noop, _noop, _noop, _noop)
        select = view.children[0]
        self.assertEqual(select.options, options)


class WatchDestinationStepViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_channel_select_create_thread_skip_back_save_and_cancel(self) -> None:
        view = WatchDestinationStepView(_SAMPLE_DESTINATION_OPTIONS, _noop, _noop, _noop, _noop, _noop, _noop)
        self.assertEqual(
            [getattr(child, "label", None) or getattr(child, "custom_id", None) for child in view.children],
            [
                "wpm_setup_watch_destination_channel_select",
                "Create New Thread (Recommended)",
                "Skip for Now",
                "Back",
                "Save & Finish Later",
                "Cancel Setup",
            ],
        )

    async def test_create_thread_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_create_thread(interaction) -> None:
            calls.append("create_thread")

        view = WatchDestinationStepView(
            _SAMPLE_DESTINATION_OPTIONS, _noop, on_create_thread, _noop, _noop, _noop, _noop
        )
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["create_thread"])

    async def test_skip_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_skip(interaction) -> None:
            calls.append("skip")

        view = WatchDestinationStepView(_SAMPLE_DESTINATION_OPTIONS, _noop, _noop, on_skip, _noop, _noop, _noop)
        await view.children[2].callback(interaction=object())
        self.assertEqual(calls, ["skip"])

    async def test_select_options_reflect_the_supplied_options_with_parent_context(self) -> None:
        options = [discord.SelectOption(label="watch-party › watched-items", value="99", default=True)]
        view = WatchDestinationStepView(options, _noop, _noop, _noop, _noop, _noop, _noop)
        select = view.children[0]
        self.assertEqual(select.options, options)


class CreateThreadNameModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_submission_forwards_the_entered_name(self) -> None:
        calls = []

        async def on_submit(interaction, name) -> None:
            calls.append(name)

        modal = CreateThreadNameModal(on_submit)
        modal.name_input._value = "Watched Item Archive"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, ["Watched Item Archive"])

    async def test_default_prefills_the_name_field(self) -> None:
        modal = CreateThreadNameModal(_noop, default="Watched Item Archive")
        self.assertEqual(modal.name_input.default, "Watched Item Archive")


class HomeChannelChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_create_new_use_existing_back_save_and_cancel(self) -> None:
        view = HomeChannelChoiceView(_noop, _noop, _noop, _noop, _noop)
        self.assertEqual(
            [child.label for child in view.children],
            [
                "Create New Channel (Recommended)",
                "Use Existing Channel",
                "Back",
                "Save & Finish Later",
                "Cancel Setup",
            ],
        )

    async def test_create_new_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_create_new(interaction) -> None:
            calls.append("create_new")

        view = HomeChannelChoiceView(on_create_new, _noop, _noop, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["create_new"])

    async def test_use_existing_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_use_existing(interaction) -> None:
            calls.append("use_existing")

        view = HomeChannelChoiceView(_noop, on_use_existing, _noop, _noop, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["use_existing"])


class HomeChannelNameModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_defaults_to_watch_party(self) -> None:
        modal = HomeChannelNameModal(_noop)
        self.assertEqual(modal.name_input.default, "Watch Party")

    async def test_default_can_be_overridden(self) -> None:
        modal = HomeChannelNameModal(_noop, default="Custom Name")
        self.assertEqual(modal.name_input.default, "Custom Name")

    async def test_submission_forwards_the_entered_name(self) -> None:
        calls = []

        async def on_submit(interaction, name) -> None:
            calls.append(name)

        modal = HomeChannelNameModal(on_submit)
        modal.name_input._value = "Watch Party"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, ["Watch Party"])


class SetupPreparationViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_begin_setup_and_cancel_buttons(self) -> None:
        view = SetupPreparationView(_noop, _noop)
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Begin Setup", "wpm_setup_preparation_begin"),
                ("Cancel Setup", "wpm_setup_cancel"),
            ],
        )

    async def test_begin_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_begin(interaction) -> None:
            calls.append("begin")

        view = SetupPreparationView(on_begin, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["begin"])

    async def test_cancel_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_cancel(interaction) -> None:
            calls.append("cancel")

        view = SetupPreparationView(_noop, on_cancel)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["cancel"])


class ModalStepIntroViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_a_configure_button_with_the_given_label_and_id(self) -> None:
        view = ModalStepIntroView(
            _noop, _noop, _noop, _noop, button_label="Set Voting Defaults", custom_id="wpm_test_configure"
        )
        self.assertEqual(view.children[0].label, "Set Voting Defaults")
        self.assertEqual(view.children[0].custom_id, "wpm_test_configure")
        self.assertIsInstance(view.children[-1], SetupCancelButton)
        self.assertTrue(any(isinstance(child, SetupBackButton) for child in view.children))
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))

    async def test_configure_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_configure(interaction) -> None:
            calls.append("configure")

        view = ModalStepIntroView(on_configure, _noop, _noop, _noop, button_label="Go", custom_id="wpm_test_configure")
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["configure"])

    async def test_back_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append("back")

        view = ModalStepIntroView(_noop, on_back, _noop, _noop, button_label="Go", custom_id="wpm_test_configure")
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        await back_button.callback(interaction=object())
        self.assertEqual(calls, ["back"])


class VotingDefaultsModalTests(unittest.IsolatedAsyncioTestCase):
    """Guild-wide only (reused unchanged by /config's Voting Defaults
    section) -- never a per-collection field like the "I Won't Watch"
    threshold; see SetupVotingDefaultsModalTests below for the Setup
    Wizard's own variant that bundles one in.
    """

    async def test_has_exactly_two_fields_both_text_inputs(self) -> None:
        # Live-Discord regression: a Select embedded in a modal
        # constructs without error locally, but Discord's API rejects
        # the resulting payload with a 400 "Invalid Form Body" at
        # submission time. This modal must contain TextInput components
        # only -- Candidate Selection Mode and Visibility are both
        # collected beforehand, on VotingDefaultsIntroView.
        modal = VotingDefaultsModal(_noop)
        self.assertEqual(len(modal.children), 2)
        self.assertTrue(all(isinstance(child, discord.ui.TextInput) for child in modal.children))
        self.assertEqual(modal.candidate_count_input.default, "3")
        self.assertEqual(modal.duration_input.default, "1d")

    async def test_submission_forwards_both_values(self) -> None:
        calls = []

        async def on_submit(interaction, candidate_count, duration_text) -> None:
            calls.append((candidate_count, duration_text))

        modal = VotingDefaultsModal(on_submit)
        modal.candidate_count_input._value = "4"
        modal.duration_input._value = "10"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, [("4", "10")])

    async def test_uses_the_supplied_defaults(self) -> None:
        modal = VotingDefaultsModal(_noop, defaults=("5", "2d"))
        self.assertEqual(modal.candidate_count_input.default, "5")
        self.assertEqual(modal.duration_input.default, "2d")


class SetupVotingDefaultsModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_exactly_three_fields_all_text_inputs(self) -> None:
        modal = SetupVotingDefaultsModal(_noop)
        self.assertEqual(len(modal.children), 3)
        self.assertTrue(all(isinstance(child, discord.ui.TextInput) for child in modal.children))
        self.assertEqual(modal.candidate_count_input.default, "3")
        self.assertEqual(modal.duration_input.default, "1d")
        self.assertEqual(modal.rejection_threshold_input.default, "2")

    async def test_submission_forwards_all_three_values(self) -> None:
        calls = []

        async def on_submit(interaction, candidate_count, duration_text, rejection_threshold_text) -> None:
            calls.append((candidate_count, duration_text, rejection_threshold_text))

        modal = SetupVotingDefaultsModal(on_submit)
        modal.candidate_count_input._value = "4"
        modal.duration_input._value = "10"
        modal.rejection_threshold_input._value = "5"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, [("4", "10", "5")])

    async def test_uses_the_supplied_defaults(self) -> None:
        modal = SetupVotingDefaultsModal(_noop, defaults=("5", "2d", "3"))
        self.assertEqual(modal.candidate_count_input.default, "5")
        self.assertEqual(modal.duration_input.default, "2d")
        self.assertEqual(modal.rejection_threshold_input.default, "3")


class VisibilitySelectComponentTests(unittest.IsolatedAsyncioTestCase):
    async def test_displays_both_options_with_their_own_explanation(self) -> None:
        select = VisibilitySelectComponent(default=GuildVoteVisibility.VISIBLE)
        self.assertEqual(
            [option.value for option in select.options],
            [GuildVoteVisibility.VISIBLE.value, GuildVoteVisibility.BLIND.value],
        )
        self.assertTrue(all(option.description for option in select.options))

    async def test_default_option_matches_the_requested_default(self) -> None:
        select = VisibilitySelectComponent(default=GuildVoteVisibility.BLIND)
        defaults = {option.value: option.default for option in select.options}
        self.assertTrue(defaults[GuildVoteVisibility.BLIND.value])
        self.assertFalse(defaults[GuildVoteVisibility.VISIBLE.value])

    async def test_selected_falls_back_to_default_when_never_touched(self) -> None:
        select = VisibilitySelectComponent(default=GuildVoteVisibility.BLIND)
        self.assertEqual(select.selected, GuildVoteVisibility.BLIND)

    async def test_selected_reflects_a_made_choice(self) -> None:
        select = VisibilitySelectComponent(default=GuildVoteVisibility.VISIBLE)
        select._values = [GuildVoteVisibility.BLIND.value]
        self.assertEqual(select.selected, GuildVoteVisibility.BLIND)

    async def test_every_option_serializes_within_discord_limits(self) -> None:
        select = VisibilitySelectComponent(default=GuildVoteVisibility.VISIBLE)
        view = discord.ui.View()
        view.add_item(select)
        self.assertEqual(find_oversized_view_component_fields(view), [])


class CandidateSelectionSelectComponentTests(unittest.IsolatedAsyncioTestCase):
    async def test_displays_all_three_modes_with_favor_new_additions_recommended(self) -> None:
        select = CandidateSelectionSelectComponent(default=CandidateSelectionMode.FAVOR_NEW_ADDITIONS)
        self.assertEqual(
            [option.value for option in select.options],
            [
                CandidateSelectionMode.FAVOR_NEW_ADDITIONS.value,
                CandidateSelectionMode.FAVOR_OLDER_ADDITIONS.value,
                CandidateSelectionMode.INFINITE_POOL.value,
            ],
        )
        self.assertEqual(
            [option.label for option in select.options],
            ["Favor New Additions (Recommended)", "Favor Older Additions", "Pure Random"],
        )

    async def test_default_option_matches_the_requested_default(self) -> None:
        select = CandidateSelectionSelectComponent(default=CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)
        defaults = {option.value: option.default for option in select.options}
        self.assertTrue(defaults[CandidateSelectionMode.FAVOR_OLDER_ADDITIONS.value])
        self.assertFalse(defaults[CandidateSelectionMode.FAVOR_NEW_ADDITIONS.value])
        self.assertFalse(defaults[CandidateSelectionMode.INFINITE_POOL.value])

    async def test_selected_falls_back_to_default_when_never_touched(self) -> None:
        select = CandidateSelectionSelectComponent(default=CandidateSelectionMode.INFINITE_POOL)
        self.assertEqual(select.selected, CandidateSelectionMode.INFINITE_POOL)

    async def test_selected_reflects_a_made_choice(self) -> None:
        select = CandidateSelectionSelectComponent(default=CandidateSelectionMode.FAVOR_NEW_ADDITIONS)
        select._values = [CandidateSelectionMode.FAVOR_OLDER_ADDITIONS.value]
        self.assertEqual(select.selected, CandidateSelectionMode.FAVOR_OLDER_ADDITIONS)

    async def test_every_option_serializes_within_discord_limits(self) -> None:
        # TASK E regression: CANDIDATE_SELECTION_HELP_TEXT[FAVOR_OLDER_
        # ADDITIONS] was a static, name-independent 104-character
        # description -- 4 over Discord's 100 (UTF-16) unit limit --
        # built via a raw discord.SelectOption(...) that bypassed
        # build_safe_select_option() entirely (see domain/suggestion_
        # database_configuration.py). This is the live production bug:
        # it reproduces with zero dependency on any channel/thread name,
        # on every guild, every time this component renders. This one
        # component is shared by /setup's Voting Defaults step, /config's
        # Nominee Selection screen, and /vote start's Customize This Vote
        # (see CandidateSelectionSelectComponent's own docstring) -- a
        # single test here guards all three call sites at once.
        for default in NOMINEE_SELECTION_MODE_ORDER:
            select = CandidateSelectionSelectComponent(default=default)
            view = discord.ui.View()
            view.add_item(select)
            self.assertEqual(find_oversized_view_component_fields(view), [])


class VotingDefaultsIntroViewTests(unittest.IsolatedAsyncioTestCase):
    def _make_view(self, on_configure=_noop, **kwargs) -> "VotingDefaultsIntroView":
        defaults = dict(
            default_candidate_selection=CandidateSelectionMode.FAVOR_NEW_ADDITIONS,
            default_visibility=GuildVoteVisibility.VISIBLE,
        )
        defaults.update(kwargs)
        return VotingDefaultsIntroView(on_configure, _noop, _noop, _noop, **defaults)

    async def test_has_both_selects_configure_back_save_and_cancel(self) -> None:
        view = self._make_view()
        self.assertEqual(len(view.children), 6)
        self.assertEqual(view.children[0].custom_id, "wpm_setup_voting_candidate_selection_select")
        self.assertEqual(view.children[1].custom_id, "wpm_voting_defaults_visibility_select")
        self.assertEqual(view.children[2].label, "Set Voting Defaults")
        self.assertIsInstance(view.children[-1], SetupCancelButton)
        self.assertTrue(any(isinstance(child, SetupBackButton) for child in view.children))
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))

    async def test_configure_forwards_the_selected_candidate_selection_and_visibility(self) -> None:
        calls = []

        async def on_configure(interaction, candidate_selection, visibility) -> None:
            calls.append((candidate_selection, visibility))

        view = self._make_view(on_configure)
        view.candidate_selection_select._values = [CandidateSelectionMode.INFINITE_POOL.value]
        view.visibility_select._values = [GuildVoteVisibility.BLIND.value]
        await view.children[2].callback(interaction=object())
        self.assertEqual(calls, [(CandidateSelectionMode.INFINITE_POOL, GuildVoteVisibility.BLIND)])

    async def test_configure_uses_defaults_when_neither_select_is_touched(self) -> None:
        calls = []

        async def on_configure(interaction, candidate_selection, visibility) -> None:
            calls.append((candidate_selection, visibility))

        view = self._make_view(
            on_configure,
            default_candidate_selection=CandidateSelectionMode.FAVOR_OLDER_ADDITIONS,
            default_visibility=GuildVoteVisibility.BLIND,
        )
        await view.children[2].callback(interaction=object())
        self.assertEqual(calls, [(CandidateSelectionMode.FAVOR_OLDER_ADDITIONS, GuildVoteVisibility.BLIND)])

    async def test_visibility_options_have_clear_explanations(self) -> None:
        view = self._make_view()
        descriptions = {option.value: option.description for option in view.visibility_select.options}
        self.assertIn("shown", descriptions[GuildVoteVisibility.VISIBLE.value].lower())
        self.assertIn("hidden", descriptions[GuildVoteVisibility.BLIND.value].lower())


class ReminderDefaultsModalTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_exactly_one_field_a_text_input(self) -> None:
        # Fixed-Option UX Audit: "enabled?" is now collected by
        # ReminderDefaultsChoiceView's buttons, before this modal ever
        # opens -- only the flexible lead-time value remains here (see
        # VotingDefaultsModal's own docstring for why Discord modals must
        # stay TextInput-only).
        modal = ReminderDefaultsModal(_noop)
        self.assertEqual(len(modal.children), 1)
        self.assertTrue(all(isinstance(child, discord.ui.TextInput) for child in modal.children))
        self.assertEqual(modal.minutes_input.default, "1d")

    async def test_uses_the_supplied_default(self) -> None:
        modal = ReminderDefaultsModal(_noop, defaults="2d")
        self.assertEqual(modal.minutes_input.default, "2d")

    async def test_submission_forwards_the_value(self) -> None:
        calls = []

        async def on_submit(interaction, minutes) -> None:
            calls.append(minutes)

        modal = ReminderDefaultsModal(on_submit)
        modal.minutes_input._value = "2d"
        await modal.on_submit(interaction=object())
        self.assertEqual(calls, ["2d"])


class ReminderDefaultsChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_enable_disable_back_save_and_cancel(self) -> None:
        view = ReminderDefaultsChoiceView(_noop, _noop, _noop, _noop, _noop)
        self.assertEqual(
            [getattr(child, "label", None) for child in view.children],
            [
                "Enable Vote-Ending Reminder (Recommended)",
                "Disable Vote-Ending Reminder",
                "Back",
                "Save & Finish Later",
                "Cancel Setup",
            ],
        )

    async def test_enable_is_the_recommended_primary_styled_button(self) -> None:
        view = ReminderDefaultsChoiceView(_noop, _noop, _noop, _noop, _noop)
        enable_button = view.children[0]
        self.assertEqual(enable_button.style, discord.ButtonStyle.primary)

    async def test_enable_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_enable(interaction) -> None:
            calls.append("enable")

        view = ReminderDefaultsChoiceView(on_enable, _noop, _noop, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["enable"])

    async def test_disable_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_disable(interaction) -> None:
            calls.append("disable")

        view = ReminderDefaultsChoiceView(_noop, on_disable, _noop, _noop, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["disable"])


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
    async def test_has_save_edit_section_back_save_for_later_and_cancel(self) -> None:
        view = ReviewStepView([("wash_crew_role", "WASH Crew Role")], _noop, _noop, _noop, _noop, _noop)
        self.assertEqual(len(view.children), 5)
        self.assertEqual(view.children[0].label, "Save")
        self.assertEqual(view.children[0].custom_id, "wpm_setup_review_save")
        self.assertIsInstance(view.children[-1], SetupCancelButton)
        self.assertTrue(any(isinstance(child, SetupBackButton) for child in view.children))
        self.assertTrue(any(isinstance(child, SetupSaveForLaterButton) for child in view.children))

    async def test_save_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_save(interaction) -> None:
            calls.append("save")

        view = ReviewStepView([("wash_crew_role", "WASH Crew Role")], on_save, _noop, _noop, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["save"])

    async def test_edit_section_select_forwards_the_chosen_step_value(self) -> None:
        calls = []

        async def on_edit_section(interaction, step_value) -> None:
            calls.append(step_value)

        view = ReviewStepView(
            [("wash_crew_role", "WASH Crew Role"), ("review", "Review")], _noop, on_edit_section, _noop, _noop, _noop
        )
        select = view.children[1]
        select._values = ["review"]
        await select.callback(interaction=object())
        self.assertEqual(calls, ["review"])

    async def test_back_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append("back")

        view = ReviewStepView([("wash_crew_role", "WASH Crew Role")], _noop, _noop, on_back, _noop, _noop)
        back_button = next(c for c in view.children if isinstance(c, SetupBackButton))
        await back_button.callback(interaction=object())
        self.assertEqual(calls, ["back"])


class SetupBackButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_stable_label_and_custom_id(self) -> None:
        button = SetupBackButton(_noop)
        self.assertEqual(button.label, "Back")
        self.assertEqual(button.custom_id, "wpm_setup_back")

    async def test_click_forwards_to_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append(interaction)

        button = SetupBackButton(on_back)
        await button.callback(interaction="fake-interaction")
        self.assertEqual(calls, ["fake-interaction"])


class SetupSaveForLaterButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_stable_label_and_custom_id(self) -> None:
        button = SetupSaveForLaterButton(_noop)
        self.assertEqual(button.label, "Save & Finish Later")
        self.assertEqual(button.custom_id, "wpm_setup_save_for_later")

    async def test_click_forwards_to_callback(self) -> None:
        calls = []

        async def on_save_for_later(interaction) -> None:
            calls.append(interaction)

        button = SetupSaveForLaterButton(on_save_for_later)
        await button.callback(interaction="fake-interaction")
        self.assertEqual(calls, ["fake-interaction"])


class RequesterScopedInteractionCheckTests(unittest.IsolatedAsyncioTestCase):
    """Defense-in-depth scoping (SetupWizardStepView.interaction_check),
    on top of every /setup message already being ephemeral.
    """

    class _FakeUser:
        def __init__(self, user_id: int) -> None:
            self.id = user_id

    class _FakeResponse:
        def __init__(self) -> None:
            self.sent_message = None
            self.sent_ephemeral = None

        async def send_message(self, content, ephemeral=False) -> None:
            self.sent_message = content
            self.sent_ephemeral = ephemeral

    class _FakeInteraction:
        def __init__(self, user_id: int) -> None:
            self.user = RequesterScopedInteractionCheckTests._FakeUser(user_id)
            self.response = RequesterScopedInteractionCheckTests._FakeResponse()

    async def test_allows_the_requester(self) -> None:
        view = WashCrewRoleStepView(_noop, _noop, _noop, requester_id=42)
        interaction = self._FakeInteraction(42)

        allowed = await view.interaction_check(interaction)

        self.assertTrue(allowed)
        self.assertIsNone(interaction.response.sent_message)

    async def test_blocks_a_different_user(self) -> None:
        view = WashCrewRoleStepView(_noop, _noop, _noop, requester_id=42)
        interaction = self._FakeInteraction(99)

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)
        self.assertIn("Only the person who ran this command", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_no_restriction_when_requester_id_is_unset(self) -> None:
        view = WashCrewRoleStepView(_noop, _noop, _noop)
        interaction = self._FakeInteraction(99)

        allowed = await view.interaction_check(interaction)

        self.assertTrue(allowed)

    async def test_every_step_view_accepts_and_enforces_requester_id(self) -> None:
        # A representative sample of the step views, confirming the
        # requester_id kwarg (and its enforcement) was threaded through
        # each one, not just WashCrewRoleStepView.
        views = [
            SetupPreparationView(_noop, _noop, requester_id=42),
            WatchPartyRoleStepView(_noop, _noop, _noop, _noop, requester_id=42),
            AdminChannelStepView(
                _SAMPLE_DESTINATION_OPTIONS, _noop, _noop, _noop, _noop, _noop, _noop, requester_id=42
            ),
            HomeChannelChoiceView(_noop, _noop, _noop, _noop, _noop, requester_id=42),
            SuggestionDatabaseChoiceView(_noop, _noop, _noop, _noop, _noop, requester_id=42),
            WatchDestinationStepView(
                _SAMPLE_DESTINATION_OPTIONS, _noop, _noop, _noop, _noop, _noop, _noop, requester_id=42
            ),
            ModalStepIntroView(_noop, _noop, _noop, _noop, button_label="Go", custom_id="wpm_x", requester_id=42),
            ReminderDefaultsChoiceView(_noop, _noop, _noop, _noop, _noop, requester_id=42),
            VotingDefaultsIntroView(
                _noop,
                _noop,
                _noop,
                _noop,
                default_candidate_selection=CandidateSelectionMode.FAVOR_NEW_ADDITIONS,
                default_visibility=GuildVoteVisibility.VISIBLE,
                requester_id=42,
            ),
            ReviewStepView([("wash_crew_role", "WASH Crew Role")], _noop, _noop, _noop, _noop, _noop, requester_id=42),
        ]
        for view in views:
            blocked = await view.interaction_check(self._FakeInteraction(99))
            self.assertFalse(blocked, f"{type(view).__name__} did not enforce requester_id")


if __name__ == "__main__":
    unittest.main()
