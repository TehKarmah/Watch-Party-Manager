"""Tests for the New Review Workflow's Crew Review notification view
(crew_review_view.py): the Retire/Keep Active/Reset Rejections/View
Suggestion button row shown in the Admin Channel once a suggestion
reaches its collection's "I Won't Watch" threshold.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import discord

from watch_party_manager.crew_review_view import (
    CrewReviewView,
    KeepActiveCrewReviewButton,
    ResetRejectionsCrewReviewButton,
    RetireCrewReviewButton,
    ViewSuggestionLinkButton,
    build_keep_active_button_custom_id,
    build_reset_rejections_button_custom_id,
    build_retire_button_custom_id,
)


async def _noop(interaction, suggestion_id) -> None:
    pass


class CustomIdBuilderTests(unittest.TestCase):
    def test_each_action_gets_a_distinct_custom_id(self) -> None:
        ids = {
            build_retire_button_custom_id(1),
            build_keep_active_button_custom_id(1),
            build_reset_rejections_button_custom_id(1),
        }
        self.assertEqual(len(ids), 3)

    def test_custom_ids_encode_the_suggestion_id(self) -> None:
        self.assertIn("42", build_retire_button_custom_id(42))
        self.assertIn("42", build_keep_active_button_custom_id(42))
        self.assertIn("42", build_reset_rejections_button_custom_id(42))


class CrewReviewViewTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_a_non_positive_suggestion_id(self) -> None:
        with self.assertRaises(ValueError):
            CrewReviewView(0, _noop, _noop, _noop)

    def test_has_four_buttons_when_a_suggestion_url_is_known(self) -> None:
        view = CrewReviewView(1, _noop, _noop, _noop, suggestion_url="https://discord.com/channels/1/2/3")
        self.assertEqual(len(view.children), 4)
        self.assertTrue(any(isinstance(child, ViewSuggestionLinkButton) for child in view.children))

    def test_has_three_buttons_when_no_suggestion_url_is_known(self) -> None:
        view = CrewReviewView(1, _noop, _noop, _noop)
        self.assertEqual(len(view.children), 3)
        self.assertFalse(any(isinstance(child, ViewSuggestionLinkButton) for child in view.children))

    def test_view_suggestion_is_a_link_style_button_pointing_at_the_url(self) -> None:
        url = "https://discord.com/channels/1/2/3"
        view = CrewReviewView(1, _noop, _noop, _noop, suggestion_url=url)
        link_button = next(child for child in view.children if isinstance(child, ViewSuggestionLinkButton))
        self.assertEqual(link_button.style, discord.ButtonStyle.link)
        self.assertEqual(link_button.url, url)

    def test_is_persistent_with_no_timeout(self) -> None:
        view = CrewReviewView(1, _noop, _noop, _noop)
        self.assertIsNone(view.timeout)

    async def test_retire_click_calls_on_retire_with_the_suggestion_id(self) -> None:
        calls = []

        async def on_retire(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        view = CrewReviewView(7, on_retire, _noop, _noop)
        retire_button = next(child for child in view.children if isinstance(child, RetireCrewReviewButton))
        await retire_button.callback(interaction=object())

        self.assertEqual(calls, [7])

    async def test_keep_active_click_calls_on_keep_active_with_the_suggestion_id(self) -> None:
        calls = []

        async def on_keep_active(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        view = CrewReviewView(7, _noop, on_keep_active, _noop)
        button = next(child for child in view.children if isinstance(child, KeepActiveCrewReviewButton))
        await button.callback(interaction=object())

        self.assertEqual(calls, [7])

    async def test_reset_rejections_click_calls_on_reset_rejections_with_the_suggestion_id(self) -> None:
        calls = []

        async def on_reset_rejections(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        view = CrewReviewView(7, _noop, _noop, on_reset_rejections)
        button = next(
            child for child in view.children if isinstance(child, ResetRejectionsCrewReviewButton)
        )
        await button.callback(interaction=object())

        self.assertEqual(calls, [7])


if __name__ == "__main__":
    unittest.main()
