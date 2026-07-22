"""Tests for FR-029's Discord UI components (config_view.py).

Mirrors test_setup_wizard_view.py's pattern: constructing each view and
confirming its components carry stable custom_ids/labels and forward
selections/clicks to the supplied callback. All /config logic lives in
services/config_service.py and bot.py's wiring around it.
"""

import unittest

from watch_party_manager.config_view import (
    BackToMenuButton,
    BackToMenuOnlyView,
    CONFIG_VIEW_TIMEOUT_SECONDS,
    ConfigDatabaseSectionView,
    ConfigJoinModeSectionView,
    ConfigMainMenuView,
    ConfigModalRetryView,
    ConfigRoleSectionView,
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


class ConfigWatchDestinationSectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_channel_select_clear_and_back(self) -> None:
        view = ConfigWatchDestinationSectionView(_noop, _noop, _noop)
        self.assertEqual(len(view.children), 3)
        self.assertEqual(view.children[0].custom_id, "wpm_config_watch_destination_channel_select")
        self.assertEqual(view.children[1].label, "Clear Destination")
        self.assertEqual(view.children[2].label, "Back to Menu")

    async def test_clear_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_skip(interaction) -> None:
            calls.append("skip")

        view = ConfigWatchDestinationSectionView(_noop, on_skip, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["skip"])


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
