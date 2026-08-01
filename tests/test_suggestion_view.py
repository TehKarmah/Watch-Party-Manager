import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.suggestion_view import (
    RejectSuggestionButton,
    SuggestionView,
    WatchedSuggestionButton,
    build_reject_button_label,
    build_watched_button_custom_id,
)


def make_watch_item(
    *, id: int = 1, status: WatchItemStatus = WatchItemStatus.SUGGESTED, rejected_by=()
) -> WatchItem:
    watch_item = WatchItem(title="The Matrix", media_type=MediaType.MOVIE, id=id, status=status)
    for discord_user_id in rejected_by:
        watch_item.journey.record_rejection(discord_user_id)
    return watch_item


class BuildRejectButtonLabelTests(unittest.TestCase):
    def test_active_label_matches_the_documented_format(self) -> None:
        self.assertEqual(
            build_reject_button_label(1, 2, archived=False), "I WON'T WATCH: 1 / 2"
        )

    def test_active_label_reflects_zero_rejections(self) -> None:
        self.assertEqual(
            build_reject_button_label(0, 2, archived=False), "I WON'T WATCH: 0 / 2"
        )

    def test_archived_label_clearly_indicates_archived(self) -> None:
        label = build_reject_button_label(2, 2, archived=True)
        self.assertIn("Archived", label)
        self.assertIn("2 / 2", label)

    def test_watched_label_clearly_indicates_watched_not_archived(self) -> None:
        label = build_reject_button_label(2, 2, archived=False, watched=True)
        self.assertIn("Watched", label)
        self.assertNotIn("Archived", label)
        self.assertIn("2 / 2", label)

    def test_pending_review_label_is_disabled_and_shows_no_counter(self) -> None:
        label = build_reject_button_label(2, 2, archived=False, pending_review=True)
        self.assertEqual(label, "Review Pending — I WON'T WATCH")

    def test_pending_review_label_is_a_single_line(self) -> None:
        # Discord button labels render as a single line -- an embedded
        # "\n" reads as a stray character rather than an actual break.
        label = build_reject_button_label(2, 2, archived=False, pending_review=True)
        self.assertNotIn("\n", label)

    def test_pending_review_takes_priority_over_archived_and_watched(self) -> None:
        label = build_reject_button_label(2, 2, archived=True, watched=True, pending_review=True)
        self.assertEqual(label, "Review Pending — I WON'T WATCH")


class SuggestionViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction, suggestion_id) -> None:
        pass

    async def test_creates_exactly_two_buttons(self) -> None:
        watch_item = make_watch_item()

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertEqual(len(view.children), 2)

    async def test_button_custom_id_encodes_the_suggestion_id(self) -> None:
        watch_item = make_watch_item(id=42)

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertIn("42", view.children[0].custom_id)

    async def test_button_label_shows_the_current_count_and_threshold(self) -> None:
        watch_item = make_watch_item(rejected_by=[111])

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertEqual(view.children[0].label, "I WON'T WATCH: 1 / 2")

    async def test_reject_button_is_disabled_for_a_pending_review_suggestion(self) -> None:
        watch_item = make_watch_item(status=WatchItemStatus.PENDING_CREW_REVIEW, rejected_by=[111, 222])

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertTrue(view.children[0].disabled)
        self.assertEqual(view.children[0].label, "Review Pending — I WON'T WATCH")

    async def test_watched_button_is_still_enabled_for_a_pending_review_suggestion(self) -> None:
        watch_item = make_watch_item(status=WatchItemStatus.PENDING_CREW_REVIEW, rejected_by=[111, 222])

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertFalse(view.children[1].disabled)

    async def test_button_is_enabled_for_an_active_suggestion(self) -> None:
        watch_item = make_watch_item(status=WatchItemStatus.SUGGESTED)

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertFalse(view.children[0].disabled)

    async def test_button_is_disabled_for_an_archived_suggestion(self) -> None:
        watch_item = make_watch_item(status=WatchItemStatus.ARCHIVED, rejected_by=[111, 222])

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertTrue(view.children[0].disabled)

    async def test_archived_button_label_indicates_archived(self) -> None:
        watch_item = make_watch_item(status=WatchItemStatus.ARCHIVED, rejected_by=[111, 222])

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertIn("Archived", view.children[0].label)

    async def test_reject_button_is_disabled_for_a_watched_suggestion(self) -> None:
        watch_item = make_watch_item(status=WatchItemStatus.WATCHED, rejected_by=[111])

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertTrue(view.children[0].disabled)
        self.assertIn("Watched", view.children[0].label)
        self.assertNotIn("Archived", view.children[0].label)

    async def test_view_has_no_timeout_making_it_persistent(self) -> None:
        watch_item = make_watch_item()

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

        self.assertIsNone(view.timeout)

    async def test_rejects_a_suggestion_without_a_persisted_id(self) -> None:
        watch_item = WatchItem(title="Unpersisted", media_type=MediaType.MOVIE)

        with self.assertRaisesRegex(ValueError, "positive"):
            SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=self._noop)

    async def test_button_click_calls_on_toggle_with_the_correct_suggestion_id(self) -> None:
        watch_item = make_watch_item(id=7)
        calls = []

        async def spy(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        view = SuggestionView(watch_item, threshold=2, on_toggle=spy, on_watched=self._noop)
        await view.children[0].callback(interaction=object())

        self.assertEqual(calls, [7])

    async def test_watched_button_click_calls_on_watched_with_the_correct_suggestion_id(self) -> None:
        watch_item = make_watch_item(id=7)
        calls = []

        async def spy(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        view = SuggestionView(watch_item, threshold=2, on_toggle=self._noop, on_watched=spy)
        await view.children[1].callback(interaction=object())

        self.assertEqual(calls, [7])


class RejectSuggestionButtonTests(unittest.TestCase):
    async def _noop(self, interaction, suggestion_id) -> None:
        pass

    def test_custom_id_encodes_the_suggestion_id(self) -> None:
        button = RejectSuggestionButton(
            suggestion_id=42, rejection_count=0, threshold=2, archived=False, on_toggle=self._noop
        )
        self.assertIn("42", button.custom_id)


class WatchedSuggestionButtonTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction, suggestion_id) -> None:
        pass

    def test_custom_id_encodes_the_suggestion_id(self) -> None:
        button = WatchedSuggestionButton(suggestion_id=42, watched=False, on_click=self._noop)
        self.assertIn("42", button.custom_id)
        self.assertEqual(button.custom_id, build_watched_button_custom_id(42))

    def test_enabled_and_not_yet_watched(self) -> None:
        button = WatchedSuggestionButton(suggestion_id=1, watched=False, on_click=self._noop)
        self.assertFalse(button.disabled)

    def test_active_label_is_mark_as_watched(self) -> None:
        button = WatchedSuggestionButton(suggestion_id=1, watched=False, on_click=self._noop)
        self.assertEqual(button.label, "Mark as Watched")

    def test_disabled_once_watched(self) -> None:
        button = WatchedSuggestionButton(suggestion_id=1, watched=True, on_click=self._noop)
        self.assertTrue(button.disabled)

    def test_watched_label_stays_past_tense_not_mark_as_watched(self) -> None:
        button = WatchedSuggestionButton(suggestion_id=1, watched=True, on_click=self._noop)
        self.assertEqual(button.label, "✅ Watched")

    async def test_click_calls_on_click_with_the_correct_suggestion_id(self) -> None:
        calls = []

        async def spy(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        button = WatchedSuggestionButton(suggestion_id=9, watched=False, on_click=spy)
        await button.callback(interaction=object())

        self.assertEqual(calls, [9])


if __name__ == "__main__":
    unittest.main()
