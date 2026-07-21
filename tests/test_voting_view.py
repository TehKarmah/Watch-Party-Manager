import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.voting_view import (
    BUTTON_LABEL_MAX_LENGTH,
    MAX_NOMINEE_BUTTONS,
    NomineeButton,
    VotingView,
    build_nominee_button_label,
)


def make_candidates(count: int) -> list[WatchItem]:
    return [
        WatchItem(title=f"Movie {index}", media_type=MediaType.MOVIE, id=index)
        for index in range(1, count + 1)
    ]


class ButtonLabelTests(unittest.TestCase):
    def test_short_titles_are_kept_intact(self) -> None:
        self.assertEqual(build_nominee_button_label(1, "The Matrix"), "1 The Matrix")

    def test_label_leads_with_the_position_number(self) -> None:
        # FR-025: "[1 Brazil]" -- number, space, title, no punctuation.
        self.assertEqual(build_nominee_button_label(1, "Brazil (1985)"), "1 Brazil (1985)")
        self.assertEqual(build_nominee_button_label(2, "Big (1988)"), "2 Big (1988)")
        self.assertEqual(build_nominee_button_label(3, "Rango (2011)"), "3 Rango (2011)")

    def test_titles_at_exactly_the_limit_are_kept_intact(self) -> None:
        prefix = "1 "
        title = "A" * (BUTTON_LABEL_MAX_LENGTH - len(prefix))

        label = build_nominee_button_label(1, title)

        self.assertEqual(label, prefix + title)
        self.assertEqual(len(label), BUTTON_LABEL_MAX_LENGTH)

    def test_titles_over_the_limit_are_truncated(self) -> None:
        title = "A" * (BUTTON_LABEL_MAX_LENGTH + 20)

        label = build_nominee_button_label(1, title)

        self.assertLessEqual(len(label), BUTTON_LABEL_MAX_LENGTH)
        self.assertTrue(label.startswith("1 "))
        self.assertTrue(label.endswith("…"))

    def test_a_larger_position_number_still_fits_within_the_limit(self) -> None:
        prefix = "10 "
        title = "A" * (BUTTON_LABEL_MAX_LENGTH - len(prefix) + 20)

        label = build_nominee_button_label(10, title)

        self.assertLessEqual(len(label), BUTTON_LABEL_MAX_LENGTH)
        self.assertTrue(label.startswith("10 "))


class VotingViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction, suggestion_id) -> None:
        pass

    async def test_creates_one_button_per_candidate(self) -> None:
        candidates = make_candidates(3)

        view = VotingView(candidates, on_vote=self._noop)

        self.assertEqual(len(view.children), 3)

    async def test_button_labels_include_the_position_and_the_candidate_title(self) -> None:
        candidates = make_candidates(2)

        view = VotingView(candidates, on_vote=self._noop)

        labels = [button.label for button in view.children]
        self.assertEqual(labels, ["1 Movie 1", "2 Movie 2"])

    async def test_buttons_are_keyed_to_the_correct_suggestion_id(self) -> None:
        candidates = make_candidates(2)

        view = VotingView(candidates, on_vote=self._noop)

        suggestion_ids = [button.suggestion_id for button in view.children]
        self.assertEqual(suggestion_ids, [1, 2])

    async def test_button_numbering_follows_candidate_order_not_suggestion_id(self) -> None:
        # Candidate order (and therefore button numbering) is independent
        # of the underlying suggestion IDs -- e.g. nominees selected out
        # of ID order must still be numbered 1, 2, 3 in display order.
        candidates = [
            WatchItem(title="Third Suggested", media_type=MediaType.MOVIE, id=30),
            WatchItem(title="First Suggested", media_type=MediaType.MOVIE, id=5),
        ]

        view = VotingView(candidates, on_vote=self._noop)

        labels = [button.label for button in view.children]
        self.assertEqual(labels, ["1 Third Suggested", "2 First Suggested"])

    async def test_rejects_more_than_the_discord_component_limit(self) -> None:
        candidates = make_candidates(MAX_NOMINEE_BUTTONS + 1)

        with self.assertRaisesRegex(ValueError, "at most"):
            VotingView(candidates, on_vote=self._noop)

    async def test_accepts_any_sequence_of_candidates(self) -> None:
        candidates = tuple(make_candidates(3))

        view = VotingView(candidates, on_vote=self._noop)

        self.assertEqual(len(view.children), 3)

    async def test_rejects_candidate_without_a_persisted_id(self) -> None:
        candidates = [WatchItem(title="Unpersisted", media_type=MediaType.MOVIE)]

        with self.assertRaisesRegex(ValueError, "positive suggestion ID"):
            VotingView(candidates, on_vote=self._noop)

    async def test_rejects_duplicate_candidate_ids(self) -> None:
        candidates = [
            WatchItem(title="First", media_type=MediaType.MOVIE, id=1),
            WatchItem(title="Second", media_type=MediaType.MOVIE, id=1),
        ]

        with self.assertRaisesRegex(ValueError, "unique suggestion IDs"):
            VotingView(candidates, on_vote=self._noop)

    async def test_button_click_calls_on_vote_with_the_correct_suggestion_id(self) -> None:
        candidates = make_candidates(2)
        calls = []

        async def spy(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        view = VotingView(candidates, on_vote=spy)
        second_button = view.children[1]

        await second_button.callback(interaction=object())

        self.assertEqual(calls, [2])

    async def test_each_button_click_is_independent(self) -> None:
        candidates = make_candidates(2)
        calls = []

        async def spy(interaction, suggestion_id) -> None:
            calls.append(suggestion_id)

        view = VotingView(candidates, on_vote=spy)
        await view.children[0].callback(interaction=object())
        await view.children[1].callback(interaction=object())

        self.assertEqual(calls, [1, 2])


class NomineeButtonTests(unittest.TestCase):
    async def _noop(self, interaction, suggestion_id) -> None:
        pass

    def test_custom_id_encodes_the_suggestion_id(self) -> None:
        button = NomineeButton(position=1, suggestion_id=42, title="The Matrix", on_vote=self._noop)
        self.assertIn("42", button.custom_id)

    def test_label_encodes_the_position_not_the_suggestion_id(self) -> None:
        button = NomineeButton(position=1, suggestion_id=42, title="The Matrix", on_vote=self._noop)
        self.assertEqual(button.label, "1 The Matrix")
        self.assertNotIn("42", button.label)


if __name__ == "__main__":
    unittest.main()
