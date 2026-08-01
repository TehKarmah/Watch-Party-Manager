"""Tests for the shared /database add and /database move UI components
(Command Structure Cleanup, pre-v1).
"""

from __future__ import annotations

import unittest

import discord

from watch_party_manager.database_admin_view import (
    CollectionManagementMenuView,
    CollectionTypeSelectionView,
    DestinationChoiceView,
    ExistingThreadSelectView,
)


async def _noop(*args, **kwargs) -> None:
    return None


class CollectionTypeSelectionViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_collections_yet_offers_every_standard_type_plus_special_and_custom(self) -> None:
        view = CollectionTypeSelectionView(set(), _noop, _noop)

        labels_and_ids = [(button.label, button.custom_id) for button in view.children]

        self.assertEqual(
            labels_and_ids,
            [
                ("Movies (Recommended)", "wpm_database_add_type_movies"),
                ("TV Shows", "wpm_database_add_type_tv_shows"),
                ("Anime", "wpm_database_add_type_anime"),
                ("Holiday", "wpm_database_add_type_holiday"),
                ("Documentaries", "wpm_database_add_type_documentaries"),
                ("Horror", "wpm_database_add_type_horror"),
                ("Special Collection", "wpm_database_add_type_special"),
                ("Custom", "wpm_database_add_type_custom"),
                ("Cancel", "wpm_database_add_cancel"),
            ],
        )

    async def test_movies_button_uses_the_primary_recommended_style(self) -> None:
        view = CollectionTypeSelectionView(set(), _noop, _noop)
        movies_button = next(button for button in view.children if button.custom_id == "wpm_database_add_type_movies")
        self.assertEqual(movies_button.style, discord.ButtonStyle.primary)

    async def test_a_used_standard_type_is_not_offered(self) -> None:
        view = CollectionTypeSelectionView({"movies"}, _noop, _noop)

        custom_ids = [button.custom_id for button in view.children]

        self.assertNotIn("wpm_database_add_type_movies", custom_ids)
        self.assertIn("wpm_database_add_type_tv_shows", custom_ids)

    async def test_the_next_unused_standard_type_becomes_the_recommended_choice(self) -> None:
        view = CollectionTypeSelectionView({"movies"}, _noop, _noop)

        tv_shows_button = next(
            button for button in view.children if button.custom_id == "wpm_database_add_type_tv_shows"
        )

        self.assertEqual(tv_shows_button.label, "TV Shows (Recommended)")
        self.assertEqual(tv_shows_button.style, discord.ButtonStyle.primary)

    async def test_special_collection_and_custom_remain_available_when_every_standard_type_is_used(self) -> None:
        used = {"movies", "tv_shows", "anime", "holiday", "documentaries", "horror"}
        view = CollectionTypeSelectionView(used, _noop, _noop)

        custom_ids = [button.custom_id for button in view.children]

        self.assertEqual(
            custom_ids,
            ["wpm_database_add_type_special", "wpm_database_add_type_custom", "wpm_database_add_cancel"],
        )

    async def test_choosing_a_type_forwards_its_key(self) -> None:
        received = []

        async def on_choice(interaction, key: str) -> None:
            received.append(key)

        view = CollectionTypeSelectionView(set(), on_choice, _noop)
        movies_button = next(button for button in view.children if button.custom_id == "wpm_database_add_type_movies")

        await movies_button.callback(interaction=None)

        self.assertEqual(received, ["movies"])


class DestinationChoiceViewTests(unittest.IsolatedAsyncioTestCase):
    """Collections Should Live In Threads: the destination choice is
    thread-only -- Use Current Channel and Use Existing Channel were
    removed outright (pre-release, no compatibility alias needed).
    """

    def test_has_create_new_thread_use_current_thread_use_existing_thread_and_cancel(self) -> None:
        view = DestinationChoiceView(_noop, _noop, _noop, _noop)

        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Create New Thread (Recommended)", "wpm_database_admin_destination_create_thread"),
                ("Use Current Thread", "wpm_database_admin_destination_use_current"),
                ("Use Existing Thread", "wpm_database_admin_destination_existing_thread"),
                ("Cancel", "wpm_database_admin_destination_cancel"),
            ],
        )

    def test_create_new_thread_is_the_primary_recommended_style(self) -> None:
        view = DestinationChoiceView(_noop, _noop, _noop, _noop)
        create_thread_button = next(
            button for button in view.children
            if button.custom_id == "wpm_database_admin_destination_create_thread"
        )
        self.assertEqual(create_thread_button.style, discord.ButtonStyle.primary)

    def test_cancel_uses_the_secondary_style(self) -> None:
        # UX Polish: Cancel is a safe no-op, never destructive -- it must
        # not compete visually with an actually destructive action.
        view = DestinationChoiceView(_noop, _noop, _noop, _noop)
        cancel_button = next(
            button for button in view.children if button.custom_id == "wpm_database_admin_destination_cancel"
        )
        self.assertEqual(cancel_button.style, discord.ButtonStyle.secondary)

    def test_use_current_is_enabled_by_default(self) -> None:
        view = DestinationChoiceView(_noop, _noop, _noop, _noop)
        use_current_button = next(
            button for button in view.children
            if button.custom_id == "wpm_database_admin_destination_use_current"
        )
        self.assertFalse(use_current_button.disabled)

    def test_use_current_is_disabled_when_the_current_location_is_unavailable(self) -> None:
        view = DestinationChoiceView(_noop, _noop, _noop, _noop, current_location_available=False)
        use_current_button = next(
            button for button in view.children
            if button.custom_id == "wpm_database_admin_destination_use_current"
        )
        self.assertTrue(use_current_button.disabled)

    def test_create_new_thread_is_enabled_by_default(self) -> None:
        view = DestinationChoiceView(_noop, _noop, _noop, _noop)
        create_thread_button = next(
            button for button in view.children
            if button.custom_id == "wpm_database_admin_destination_create_thread"
        )
        self.assertFalse(create_thread_button.disabled)

    def test_create_new_thread_is_disabled_when_unavailable(self) -> None:
        # Create New Thread Improvement: disabled when there's nowhere to
        # create a new thread (no Home Channel, and the current location
        # can't parent one either).
        view = DestinationChoiceView(_noop, _noop, _noop, _noop, create_new_thread_available=False)
        create_thread_button = next(
            button for button in view.children
            if button.custom_id == "wpm_database_admin_destination_create_thread"
        )
        self.assertTrue(create_thread_button.disabled)


class CollectionManagementMenuViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_every_management_action_plus_cancel(self) -> None:
        view = CollectionManagementMenuView(_noop, _noop)

        # Database Manage UX (Section 2): administrative actions ordered
        # first (roughly most- to least-frequently used), destructive
        # actions last, Cancel always the final item.
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Edit Collection", "wpm_database_manage_edit"),
                ("Backup Collection", "wpm_database_manage_backup"),
                ("Move Collection", "wpm_database_manage_move"),
                ("Restore Collection", "wpm_database_manage_restore"),
                ("Reset Collection", "wpm_database_manage_reset"),
                ("Remove Collection", "wpm_database_manage_remove"),
                ("Cancel", "wpm_database_manage_cancel"),
            ],
        )

    async def test_cancel_uses_the_secondary_style(self) -> None:
        # UX Polish: Cancel is a safe no-op -- previously styled danger
        # (red) even though the genuinely destructive actions beside it
        # (Reset Collection, Remove Collection) were plain secondary, the
        # exact inversion of what button styling should signal.
        view = CollectionManagementMenuView(_noop, _noop)
        cancel_button = next(button for button in view.children if button.custom_id == "wpm_database_manage_cancel")
        self.assertEqual(cancel_button.style, discord.ButtonStyle.secondary)

    async def test_destructive_actions_use_the_danger_style(self) -> None:
        view = CollectionManagementMenuView(_noop, _noop)
        reset_button = next(button for button in view.children if button.custom_id == "wpm_database_manage_reset")
        remove_button = next(button for button in view.children if button.custom_id == "wpm_database_manage_remove")
        self.assertEqual(reset_button.style, discord.ButtonStyle.danger)
        self.assertEqual(remove_button.style, discord.ButtonStyle.danger)

    async def test_non_destructive_actions_use_the_secondary_style(self) -> None:
        view = CollectionManagementMenuView(_noop, _noop)
        for custom_id in (
            "wpm_database_manage_move",
            "wpm_database_manage_edit",
            "wpm_database_manage_backup",
            "wpm_database_manage_restore",
        ):
            button = next(button for button in view.children if button.custom_id == custom_id)
            self.assertEqual(button.style, discord.ButtonStyle.secondary)

    async def test_choosing_an_action_forwards_its_key(self) -> None:
        received = []

        async def on_action_chosen(interaction, action: str) -> None:
            received.append(action)

        view = CollectionManagementMenuView(on_action_chosen, _noop)
        move_button = next(button for button in view.children if button.custom_id == "wpm_database_manage_move")

        await move_button.callback(interaction=None)

        self.assertEqual(received, ["move"])


class ExistingThreadSelectViewTests(unittest.TestCase):
    def test_select_is_filtered_to_thread_types_only(self) -> None:
        view = ExistingThreadSelectView(_noop, _noop)
        select = next(child for child in view.children if isinstance(child, discord.ui.ChannelSelect))
        self.assertEqual(
            set(select.channel_types), {discord.ChannelType.public_thread, discord.ChannelType.private_thread}
        )


if __name__ == "__main__":
    unittest.main()
