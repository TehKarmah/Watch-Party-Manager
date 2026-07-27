"""Tests for the Watch Party Lifecycle's winner-announcement scheduling
UI components (winner_announcement_view.py): the Schedule Watch Party/
Choose Winner to Schedule/Watch Party Scheduled button, the tie-breaking
picker, and the guided scheduling modal.
"""

from __future__ import annotations

import unittest

import discord

from watch_party_manager.winner_announcement_view import (
    ChooseWinnerSelectView,
    ChooseWinnerToScheduleButton,
    ScheduleWatchPartyButton,
    ScheduleWatchPartyModal,
    WatchPartyScheduledButton,
    WinnerAnnouncementView,
    build_choose_winner_button_custom_id,
    build_scheduled_button_custom_id,
    build_schedule_button_custom_id,
)


async def _noop(*args, **kwargs) -> None:
    return None


class WinnerAnnouncementViewTests(unittest.IsolatedAsyncioTestCase):
    def test_single_winner_shows_the_schedule_button(self) -> None:
        view = WinnerAnnouncementView(1, [42], _noop, _noop)

        self.assertEqual(len(view.children), 1)
        button = view.children[0]
        self.assertIsInstance(button, ScheduleWatchPartyButton)
        self.assertEqual(button.label, "Schedule Watch Party")
        self.assertEqual(button.custom_id, build_schedule_button_custom_id(1))
        self.assertFalse(button.disabled)

    def test_tie_shows_the_choose_winner_button(self) -> None:
        view = WinnerAnnouncementView(1, [42, 43], _noop, _noop)

        self.assertEqual(len(view.children), 1)
        button = view.children[0]
        self.assertIsInstance(button, ChooseWinnerToScheduleButton)
        self.assertEqual(button.label, "Choose Winner to Schedule")
        self.assertEqual(button.custom_id, build_choose_winner_button_custom_id(1))

    def test_already_scheduled_overrides_a_single_winner(self) -> None:
        view = WinnerAnnouncementView(1, [42], _noop, _noop, already_scheduled=True)

        button = view.children[0]
        self.assertIsInstance(button, WatchPartyScheduledButton)
        self.assertEqual(button.label, "Watch Party Scheduled")
        self.assertTrue(button.disabled)
        self.assertEqual(button.custom_id, build_scheduled_button_custom_id(1))

    def test_already_scheduled_overrides_a_tie(self) -> None:
        view = WinnerAnnouncementView(1, [42, 43], _noop, _noop, already_scheduled=True)

        button = view.children[0]
        self.assertIsInstance(button, WatchPartyScheduledButton)

    async def test_schedule_button_forwards_the_suggestion_id(self) -> None:
        received = []

        async def on_schedule(interaction, suggestion_id) -> None:
            received.append(suggestion_id)

        view = WinnerAnnouncementView(1, [42], on_schedule, _noop)
        await view.children[0].callback(interaction=None)

        self.assertEqual(received, [42])

    async def test_choose_winner_button_forwards_the_click(self) -> None:
        received = []

        async def on_choose_winner(interaction) -> None:
            received.append(interaction)

        view = WinnerAnnouncementView(1, [42, 43], _noop, on_choose_winner)
        await view.children[0].callback(interaction="fake-interaction")

        self.assertEqual(received, ["fake-interaction"])


class ChooseWinnerSelectViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_selecting_an_option_forwards_its_id(self) -> None:
        received = []

        async def on_select(interaction, suggestion_id) -> None:
            received.append(suggestion_id)

        view = ChooseWinnerSelectView([(42, "Movie A", "(2020)"), (43, "Movie B", "(2021)")], on_select)
        select = view.children[0]
        select._values = ["43"]

        await select.callback(interaction=None)

        self.assertEqual(received, [43])


class ScheduleWatchPartyModalTests(unittest.IsolatedAsyncioTestCase):
    def test_duration_input_defaults_to_the_given_text(self) -> None:
        modal = ScheduleWatchPartyModal(_noop, default_duration_text="2h 22m")

        self.assertEqual(modal.duration_input.default, "2h 22m")

    def test_duration_input_has_no_default_when_runtime_is_unknown(self) -> None:
        modal = ScheduleWatchPartyModal(_noop)

        self.assertIsNone(modal.duration_input.default)

    def test_description_input_is_optional(self) -> None:
        modal = ScheduleWatchPartyModal(_noop)

        self.assertFalse(modal.description_input.required)

    def test_when_and_duration_and_location_inputs_are_required(self) -> None:
        modal = ScheduleWatchPartyModal(_noop)

        self.assertTrue(modal.when_input.required)
        self.assertTrue(modal.duration_input.required)
        self.assertTrue(modal.location_input.required)

    async def test_submit_forwards_all_four_fields(self) -> None:
        received = []

        async def on_submit(interaction, when_text, duration_text, location_text, description_text) -> None:
            received.append((when_text, duration_text, location_text, description_text))

        modal = ScheduleWatchPartyModal(on_submit)
        modal.when_input._value = "2026-08-01 20:00"
        modal.duration_input._value = "2h"
        modal.location_input._value = "Discord Voice Chat"
        modal.description_input._value = "Bring snacks"

        await modal.on_submit(interaction=None)

        self.assertEqual(received, [("2026-08-01 20:00", "2h", "Discord Voice Chat", "Bring snacks")])


if __name__ == "__main__":
    unittest.main()
