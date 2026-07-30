"""Tests for FR-029's Discord UI components (config_view.py).

Mirrors test_setup_wizard_view.py's pattern: constructing each view and
confirming its components carry stable custom_ids/labels and forward
selections/clicks to the supplied callback. All /config logic lives in
services/config_service.py and bot.py's wiring around it.
"""

import unittest

import discord

from watch_party_manager.config_view import (
    BackToMenuButton,
    BackToMenuOnlyView,
    CONFIG_VIEW_TIMEOUT_SECONDS,
    ConfigAdminChannelSectionView,
    ConfigDatabaseSectionView,
    ConfigHomeChannelSectionView,
    ConfigJoinModeSectionView,
    ConfigMainMenuView,
    ConfigModalRetryView,
    ConfigRoleSectionView,
    ConfigSuggestionDestinationSectionView,
    ConfigWatchDestinationSectionView,
)
from watch_party_manager.domain.guild_configuration import JoinMode


async def _noop(*args) -> None:
    pass


class BackToMenuButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_stable_label_and_custom_id(self) -> None:
        button = BackToMenuButton(_noop)
        self.assertEqual(button.label, "Back to Menu")
        self.assertEqual(button.custom_id, "wpm_config_back_to_menu")

    async def test_click_forwards_to_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append(interaction)

        button = BackToMenuButton(on_back)
        await button.callback(interaction="fake-interaction")
        self.assertEqual(calls, ["fake-interaction"])


class BackToMenuOnlyViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_a_single_button_with_the_expected_timeout(self) -> None:
        view = BackToMenuOnlyView(_noop)
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.timeout, CONFIG_VIEW_TIMEOUT_SECONDS)


class ConfigMainMenuViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_a_single_section_select(self) -> None:
        view = ConfigMainMenuView([("wash_crew_role", "WASH Crew Role")], _noop)
        self.assertEqual(len(view.children), 1)
        self.assertEqual(view.children[0].custom_id, "wpm_config_section_select")

    async def test_selection_forwards_the_chosen_section_value(self) -> None:
        calls = []

        async def on_select(interaction, section_value) -> None:
            calls.append(section_value)

        view = ConfigMainMenuView(
            [("wash_crew_role", "WASH Crew Role"), ("backup_defaults", "Backup Defaults")], on_select
        )
        select = view.children[0]
        select._values = ["backup_defaults"]
        await select.callback(interaction=object())
        self.assertEqual(calls, ["backup_defaults"])

    async def test_descriptions_apply_only_to_the_matching_option(self) -> None:
        view = ConfigMainMenuView(
            [("wash_crew_role", "WASH Crew Role"), ("voting_defaults", "Voting Defaults")],
            _noop,
            descriptions={"voting_defaults": "Visible/Blind explained here"},
        )
        select = view.children[0]
        options_by_value = {option.value: option for option in select.options}
        self.assertIsNone(options_by_value["wash_crew_role"].description)
        self.assertEqual(options_by_value["voting_defaults"].description, "Visible/Blind explained here")

    async def test_no_descriptions_leaves_every_option_undescribed(self) -> None:
        view = ConfigMainMenuView([("wash_crew_role", "WASH Crew Role")], _noop)
        self.assertIsNone(view.children[0].options[0].description)


class ConfigRoleSectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_a_role_select_and_back_to_menu(self) -> None:
        view = ConfigRoleSectionView(
            _noop, _noop, custom_id="wpm_config_wash_crew_role_select", placeholder="Select a role"
        )
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.children[0].custom_id, "wpm_config_wash_crew_role_select")
        self.assertEqual(view.children[0].min_values, 1)
        self.assertEqual(view.children[1].label, "Back to Menu")

    async def test_min_values_zero_allows_clearing_the_role(self) -> None:
        view = ConfigRoleSectionView(
            _noop, _noop, custom_id="wpm_config_watch_party_role_select", placeholder="Select a role", min_values=0
        )
        self.assertEqual(view.children[0].min_values, 0)

    async def test_selection_forwards_the_chosen_role_id(self) -> None:
        calls = []

        async def on_select(interaction, role_id) -> None:
            calls.append(role_id)

        view = ConfigRoleSectionView(on_select, _noop, custom_id="x", placeholder="p")
        select = view.children[0]

        class FakeRoleValue:
            id = 222

        select._values = [FakeRoleValue()]
        await select.callback(interaction=object())
        self.assertEqual(calls, [222])

    async def test_no_selection_forwards_none(self) -> None:
        calls = []

        async def on_select(interaction, role_id) -> None:
            calls.append(role_id)

        view = ConfigRoleSectionView(on_select, _noop, custom_id="x", placeholder="p", min_values=0)
        select = view.children[0]
        await select.callback(interaction=object())
        self.assertEqual(calls, [None])

    async def test_back_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_back(interaction) -> None:
            calls.append("back")

        view = ConfigRoleSectionView(_noop, on_back, custom_id="x", placeholder="p")
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["back"])


class ConfigJoinModeSectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_a_join_mode_select_and_back_to_menu(self) -> None:
        view = ConfigJoinModeSectionView(_noop, _noop)
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.children[0].custom_id, "wpm_config_join_mode_select")

    async def test_selection_forwards_the_parsed_join_mode(self) -> None:
        calls = []

        async def on_select(interaction, join_mode) -> None:
            calls.append(join_mode)

        view = ConfigJoinModeSectionView(on_select, _noop)
        select = view.children[0]
        select._values = [JoinMode.APPROVAL.value]
        await select.callback(interaction=object())
        self.assertEqual(calls, [JoinMode.APPROVAL])


class ConfigDatabaseSectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_builds_one_option_per_database(self) -> None:
        view = ConfigDatabaseSectionView([(1, "Movies"), (2, "TV Shows")], _noop, _noop)
        select = view.children[0]
        self.assertEqual([option.value for option in select.options], ["1", "2"])

    async def test_selection_forwards_the_chosen_database_id(self) -> None:
        calls = []

        async def on_select(interaction, database_id) -> None:
            calls.append(database_id)

        view = ConfigDatabaseSectionView([(5, "Movies")], on_select, _noop)
        select = view.children[0]
        select._values = ["5"]
        await select.callback(interaction=object())
        self.assertEqual(calls, [5])

    async def test_descriptions_apply_only_to_the_matching_database(self) -> None:
        # Collections Summary: each option's description names that
        # collection's own Candidate Selection Mode.
        view = ConfigDatabaseSectionView(
            [(1, "Movies"), (2, "TV Shows")],
            _noop,
            _noop,
            descriptions={1: "Candidate Selection: Balanced Random"},
        )
        select = view.children[0]
        descriptions_by_value = {option.value: option.description for option in select.options}
        self.assertEqual(descriptions_by_value["1"], "Candidate Selection: Balanced Random")
        self.assertIsNone(descriptions_by_value["2"])

    async def test_no_descriptions_leaves_every_option_undescribed(self) -> None:
        view = ConfigDatabaseSectionView([(1, "Movies")], _noop, _noop)
        select = view.children[0]
        self.assertIsNone(select.options[0].description)


_SAMPLE_CONFIG_DESTINATION_OPTIONS = [discord.SelectOption(label="general", value="1")]


class ConfigAdminChannelSectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_channel_select_clear_and_back(self) -> None:
        view = ConfigAdminChannelSectionView(_SAMPLE_CONFIG_DESTINATION_OPTIONS, _noop, _noop, _noop)
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.children[0].custom_id, "wpm_config_admin_channel_select")
        self.assertEqual(view.children[1].label, "Clear Admin Channel")
        self.assertEqual(view.children[2].label, "Back to Menu")

    async def test_clear_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_clear(interaction) -> None:
            calls.append("clear")

        view = ConfigAdminChannelSectionView(_SAMPLE_CONFIG_DESTINATION_OPTIONS, _noop, on_clear, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["clear"])


class ConfigWatchDestinationSectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_channel_select_clear_and_back(self) -> None:
        view = ConfigWatchDestinationSectionView(_SAMPLE_CONFIG_DESTINATION_OPTIONS, _noop, _noop, _noop)
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.children[0].custom_id, "wpm_config_watch_destination_channel_select")
        self.assertEqual(view.children[1].label, "Clear Archive")
        self.assertEqual(view.children[2].label, "Back to Menu")

    async def test_clear_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_skip(interaction) -> None:
            calls.append("skip")

        view = ConfigWatchDestinationSectionView(_SAMPLE_CONFIG_DESTINATION_OPTIONS, _noop, on_skip, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["skip"])


class ConfigHomeChannelSectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_create_new_use_current_use_existing_clear_and_back(self) -> None:
        view = ConfigHomeChannelSectionView(_noop, _noop, _noop, _noop, _noop)
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Create New Channel (Recommended)", "wpm_setup_destination_create_channel"),
                ("Use Current Channel", "wpm_config_home_channel_use_current"),
                ("Use Existing Channel", "wpm_setup_destination_existing_channel"),
                ("Clear Home Channel", "wpm_config_home_channel_clear"),
                ("Back to Menu", "wpm_config_back_to_menu"),
            ],
        )

    async def test_use_current_is_enabled_by_default(self) -> None:
        view = ConfigHomeChannelSectionView(_noop, _noop, _noop, _noop, _noop)
        use_current_button = next(
            b for b in view.children if b.custom_id == "wpm_config_home_channel_use_current"
        )
        self.assertFalse(use_current_button.disabled)

    async def test_use_current_is_disabled_when_unavailable(self) -> None:
        view = ConfigHomeChannelSectionView(_noop, _noop, _noop, _noop, _noop, current_channel_available=False)
        use_current_button = next(
            b for b in view.children if b.custom_id == "wpm_config_home_channel_use_current"
        )
        self.assertTrue(use_current_button.disabled)

    async def test_clear_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_clear(interaction) -> None:
            calls.append("clear")

        view = ConfigHomeChannelSectionView(_noop, _noop, _noop, on_clear, _noop)
        clear_button = next(b for b in view.children if b.custom_id == "wpm_config_home_channel_clear")
        await clear_button.callback(interaction=object())
        self.assertEqual(calls, ["clear"])


class ConfigSuggestionDestinationSectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_select_is_restricted_to_threads_only(self) -> None:
        # Collections Should Live In Threads: a collection's suggestion
        # destination can only ever be a thread -- this also makes
        # WASH's Home Channel (always a plain text channel) structurally
        # unselectable here, on top of ConfigService's explicit rejection.
        view = ConfigSuggestionDestinationSectionView(_noop, _noop)
        select = next(child for child in view.children if isinstance(child, discord.ui.ChannelSelect))
        self.assertEqual(
            set(select.channel_types), {discord.ChannelType.public_thread, discord.ChannelType.private_thread}
        )


class ConfigModalRetryViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_retry_and_back_buttons(self) -> None:
        view = ConfigModalRetryView(_noop, _noop, button_label="Try Again", custom_id="wpm_test_retry")
        self.assertEqual(len(view.children), 2)
        self.assertEqual(view.children[0].label, "Try Again")
        self.assertEqual(view.children[0].custom_id, "wpm_test_retry")
        self.assertEqual(view.children[1].label, "Back to Menu")

    async def test_retry_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_retry(interaction) -> None:
            calls.append("retry")

        view = ConfigModalRetryView(on_retry, _noop, button_label="Try Again", custom_id="wpm_test_retry")
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["retry"])


if __name__ == "__main__":
    unittest.main()
