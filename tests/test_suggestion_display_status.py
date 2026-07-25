import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain.watch_item import MediaType, WatchItem, WatchItemStatus
from watch_party_manager.services.suggestion_display_status import (
    SUGGESTION_DISPLAY_STATUS_LABELS,
    SuggestionDisplayStatus,
    compute_display_status,
    display_status_label,
    resolve_display_status,
)


def make_item(status: WatchItemStatus = WatchItemStatus.SUGGESTED) -> WatchItem:
    return WatchItem(title="Alien", media_type=MediaType.MOVIE, status=status)


class ComputeDisplayStatusTests(unittest.TestCase):
    def test_archived_is_retired_regardless_of_cooldown(self) -> None:
        item = make_item(WatchItemStatus.ARCHIVED)
        self.assertEqual(
            SuggestionDisplayStatus.RETIRED, compute_display_status(item, in_rotation_cooldown=True)
        )
        self.assertEqual(
            SuggestionDisplayStatus.RETIRED, compute_display_status(item, in_rotation_cooldown=False)
        )

    def test_vote_winner_is_vote_winner_regardless_of_cooldown(self) -> None:
        item = make_item(WatchItemStatus.VOTE_WINNER)
        self.assertEqual(
            SuggestionDisplayStatus.VOTE_WINNER, compute_display_status(item, in_rotation_cooldown=True)
        )
        self.assertEqual(
            SuggestionDisplayStatus.VOTE_WINNER, compute_display_status(item, in_rotation_cooldown=False)
        )

    def test_suggested_in_cooldown_is_rotation_cooldown(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.ROTATION_COOLDOWN, compute_display_status(item, in_rotation_cooldown=True)
        )

    def test_suggested_not_in_cooldown_is_available(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.AVAILABLE, compute_display_status(item, in_rotation_cooldown=False)
        )


class ResolveDisplayStatusTests(unittest.TestCase):
    class _FakeRotationService:
        def __init__(self, cooldown: bool) -> None:
            self._cooldown = cooldown

        def is_in_rotation_cooldown(self, watch_item) -> bool:
            return self._cooldown

    def test_uses_the_rotation_service_when_provided(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(
            SuggestionDisplayStatus.ROTATION_COOLDOWN,
            resolve_display_status(item, self._FakeRotationService(True)),
        )

    def test_defaults_to_available_with_no_rotation_service(self) -> None:
        item = make_item(WatchItemStatus.SUGGESTED)
        self.assertEqual(SuggestionDisplayStatus.AVAILABLE, resolve_display_status(item, None))


class DisplayStatusLabelTests(unittest.TestCase):
    def test_every_status_has_a_label(self) -> None:
        for status in SuggestionDisplayStatus:
            self.assertIn(status, SUGGESTION_DISPLAY_STATUS_LABELS)

    def test_labels_match_the_specified_emoji_and_wording(self) -> None:
        self.assertEqual("🟢 Available", display_status_label(SuggestionDisplayStatus.AVAILABLE))
        self.assertEqual(
            "🟡 Rotation Cooldown", display_status_label(SuggestionDisplayStatus.ROTATION_COOLDOWN)
        )
        self.assertEqual("🟣 Vote Winner", display_status_label(SuggestionDisplayStatus.VOTE_WINNER))
        self.assertEqual("🔴 Retired", display_status_label(SuggestionDisplayStatus.RETIRED))


if __name__ == "__main__":
    unittest.main()
