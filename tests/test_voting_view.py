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
        self.assertEqual(build_nominee_button_label("The Matrix"), "The Matrix")

    def test_no_leading_nominee_number(self) -> None:
        # Release Polish Batch 2, Priority 4: no leading position number.
        self.assertEqual(build_nominee_button_label("Brazil (1985)"), "Brazil (1985)")
        self.assertEqual(build_nominee_button_label("Big (1988)"), "Big (1988)")
        self.assertEqual(build_nominee_button_label("Rango (2011)"), "Rango (2011)")

    def test_year_is_appended_exactly_once_when_missing_from_the_title(self) -> None:
        self.assertEqual(build_nominee_button_label("Brazil", 1985), "Brazil (1985)")

    def test_year_is_not_duplicated_when_already_embedded_in_the_title(self) -> None:
        self.assertEqual(build_nominee_button_label("Brazil (1985)", 1985), "Brazil (1985)")

    def test_titles_at_exactly_the_limit_are_kept_intact(self) -> None:
        title = "A" * BUTTON_LABEL_MAX_LENGTH

        label = build_nominee_button_label(title)

        self.assertEqual(label, title)
        self.assertEqual(len(label), BUTTON_LABEL_MAX_LENGTH)

    def test_titles_over_the_limit_are_truncated(self) -> None:
        title = "A" * (BUTTON_LABEL_MAX_LENGTH + 20)

        label = build_nominee_button_label(title)

        self.assertLessEqual(len(label), BUTTON_LABEL_MAX_LENGTH)
        self.assertTrue(label.endswith("…"))


class VotingViewTests(unittest.IsolatedAsyncioTestCase):
    async def _noop(self, interaction, suggestion_id) -> None:
        pass

    async def test_creates_one_button_per_candidate(self) -> None:
        candidates = make_candidates(3)

        view = VotingView(candidates, on_vote=self._noop)

        self.assertEqual(len(view.children), 3)

    async def test_button_labels_are_clean_candidate_titles(self) -> None:
        candidates = make_candidates(2)

        view = VotingView(candidates, on_vote=self._noop)

        labels = [button.label for button in view.children]
        self.assertEqual(labels, ["Movie 1", "Movie 2"])

    async def test_buttons_are_keyed_to_the_correct_suggestion_id(self) -> None:
        candidates = make_candidates(2)

        view = VotingView(candidates, on_vote=self._noop)

        suggestion_ids = [button.suggestion_id for button in view.children]
        self.assertEqual(suggestion_ids, [1, 2])

    async def test_button_order_follows_candidate_order_not_suggestion_id(self) -> None:
        # Candidate (button) order is independent of the underlying
        # suggestion IDs -- e.g. nominees selected out of ID order must
        # still render in the given candidate order.
        candidates = [
            WatchItem(title="Third Suggested", media_type=MediaType.MOVIE, id=30),
            WatchItem(title="First Suggested", media_type=MediaType.MOVIE, id=5),
        ]

        view = VotingView(candidates, on_vote=self._noop)

        labels = [button.label for button in view.children]
        self.assertEqual(labels, ["Third Suggested", "First Suggested"])

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
        button = NomineeButton(suggestion_id=42, title="The Matrix", on_vote=self._noop)
        self.assertIn("42", button.custom_id)

    def test_label_is_the_clean_title_not_the_suggestion_id(self) -> None:
        button = NomineeButton(suggestion_id=42, title="The Matrix", on_vote=self._noop)
        self.assertEqual(button.label, "The Matrix")
        self.assertNotIn("42", button.label)

    def test_label_includes_the_release_year_exactly_once(self) -> None:
        button = NomineeButton(suggestion_id=1, title="Brazil", on_vote=self._noop, release_year=1985)
        self.assertEqual(button.label, "Brazil (1985)")


if __name__ == "__main__":
    unittest.main()
