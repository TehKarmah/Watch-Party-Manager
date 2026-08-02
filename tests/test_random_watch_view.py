"""Tests for random_watch_view.py's Discord UI components: construction,
stable custom_ids/labels, and forwarding clicks/selections to the
supplied callback. All session/business logic lives in bot.py.
"""

import unittest

import discord

from watch_party_manager.random_watch_view import (
    RANDOM_WATCH_VIEW_TIMEOUT_SECONDS,
    RandomWatchInitialView,
    RandomWatchResultView,
)


async def _noop(*args, **kwargs):
    return None


class FakeResultResponse:
    def __init__(self) -> None:
        self.sent_message = None
        self.sent_ephemeral = None

    async def send_message(self, content, ephemeral=False) -> None:
        self.sent_message = content
        self.sent_ephemeral = ephemeral


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeResultInteraction:
    def __init__(self, user_id: int) -> None:
        self.user = FakeUser(user_id)
        self.response = FakeResultResponse()


class RandomWatchInitialViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_has_pick_random_and_add_filters_buttons(self) -> None:
        view = RandomWatchInitialView(_noop, _noop)
        self.assertEqual(
            [(button.label, button.custom_id) for button in view.children],
            [
                ("Pick Random Item", "wpm_random_watch_pick_initial"),
                ("Add Filters", "wpm_random_watch_add_filters"),
            ],
        )
        self.assertEqual(view.timeout, RANDOM_WATCH_VIEW_TIMEOUT_SECONDS)

    async def test_pick_random_button_uses_primary_style(self) -> None:
        view = RandomWatchInitialView(_noop, _noop)
        self.assertEqual(view.children[0].style, discord.ButtonStyle.primary)

    async def test_pick_random_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_pick(interaction) -> None:
            calls.append("pick")

        view = RandomWatchInitialView(on_pick, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["pick"])

    async def test_add_filters_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_add_filters(interaction) -> None:
            calls.append("filters")

        view = RandomWatchInitialView(_noop, on_add_filters)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["filters"])


class RandomWatchResultViewTests(unittest.IsolatedAsyncioTestCase):
    def test_includes_pick_again_change_filters_and_change_collection(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop)
        custom_ids = [child.custom_id for child in view.children]
        self.assertEqual(
            custom_ids,
            [
                "wpm_random_watch_pick_again",
                "wpm_random_watch_change_filters",
                "wpm_random_watch_result_change_collection",
            ],
        )

    def test_omits_the_link_button_when_no_original_suggestion_url(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop, original_suggestion_url=None)
        labels = [getattr(child, "label", None) for child in view.children]
        self.assertNotIn("View Original Suggestion", labels)

    def test_includes_a_link_style_button_when_a_url_is_available(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop, original_suggestion_url="https://discord.com/channels/1/2/3")
        link_button = next(c for c in view.children if getattr(c, "label", None) == "View Original Suggestion")
        self.assertEqual(link_button.style, discord.ButtonStyle.link)
        self.assertEqual(link_button.url, "https://discord.com/channels/1/2/3")

    async def test_pick_again_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_pick_again(interaction) -> None:
            calls.append("again")

        view = RandomWatchResultView(on_pick_again, _noop, _noop)
        await view.children[0].callback(interaction=object())
        self.assertEqual(calls, ["again"])

    async def test_change_filters_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_change_filters(interaction) -> None:
            calls.append("filters")

        view = RandomWatchResultView(_noop, on_change_filters, _noop)
        await view.children[1].callback(interaction=object())
        self.assertEqual(calls, ["filters"])

    async def test_change_collection_button_triggers_its_callback(self) -> None:
        calls = []

        async def on_change_collection(interaction) -> None:
            calls.append("collection")

        view = RandomWatchResultView(_noop, _noop, on_change_collection)
        await view.children[2].callback(interaction=object())
        self.assertEqual(calls, ["collection"])

    def test_row_count_stays_within_discord_limits_with_link_button(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop, original_suggestion_url="https://discord.com/channels/1/2/3")
        self.assertLessEqual(len(view.children), 5)

    # --- Public result: requester-only interaction (Section 4/8) -----------------

    async def test_only_the_requester_can_use_the_public_result_buttons(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop, requester_id=42)
        interaction = FakeResultInteraction(user_id=99)

        allowed = await view.interaction_check(interaction)

        self.assertFalse(allowed)
        self.assertIn("Only the person who ran this command", interaction.response.sent_message)
        self.assertTrue(interaction.response.sent_ephemeral)

    async def test_requester_is_allowed_to_use_the_public_result_buttons(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop, requester_id=42)
        interaction = FakeResultInteraction(user_id=42)

        allowed = await view.interaction_check(interaction)

        self.assertTrue(allowed)

    async def test_no_requester_restriction_when_unset(self) -> None:
        view = RandomWatchResultView(_noop, _noop, _noop)
        interaction = FakeResultInteraction(user_id=99)

        allowed = await view.interaction_check(interaction)

        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
