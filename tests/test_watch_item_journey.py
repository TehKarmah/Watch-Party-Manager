import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain import WatchItemJourney


class WatchItemJourneyModelTests(unittest.TestCase):
    def test_journey_defaults_are_empty_and_zeroed(self) -> None:
        journey = WatchItemJourney()

        self.assertIsNone(journey.original_suggester)
        self.assertIsNone(journey.suggestion_date)
        self.assertEqual(journey.rotation_history, ())
        self.assertEqual(journey.voting_appearances, 0)
        self.assertIsNone(journey.winning_vote)
        self.assertEqual(journey.watch_dates, ())
        self.assertEqual(journey.rewatch_count, 0)

    def test_journey_normalizes_optional_text_and_dates(self) -> None:
        journey = WatchItemJourney(
            original_suggester="  Ada  ",
            suggestion_date=date(2026, 7, 8),
        )

        self.assertEqual(journey.original_suggester, "Ada")
        self.assertEqual(journey.suggestion_date, date(2026, 7, 8))

    def test_journey_tracks_rotations_votes_and_watch_dates(self) -> None:
        journey = WatchItemJourney()

        journey.record_rotation_entry(3)
        journey.record_vote_appearance()
        journey.record_winning_vote("The Matrix")
        journey.record_watch_date(date(2026, 7, 9))
        journey.record_rewatch()

        self.assertEqual(journey.rotation_history, (3,))
        self.assertEqual(journey.voting_appearances, 1)
        self.assertEqual(journey.winning_vote, "The Matrix")
        self.assertEqual(journey.watch_dates, (date(2026, 7, 9),))
        self.assertEqual(journey.rewatch_count, 1)

    def test_journey_rejects_negative_counts(self) -> None:
        with self.assertRaises(ValueError):
            WatchItemJourney(voting_appearances=-1)

        with self.assertRaises(ValueError):
            WatchItemJourney(rewatch_count=-1)

    def test_journey_validates_rotation_entries(self) -> None:
        with self.assertRaises(TypeError):
            WatchItemJourney(rotation_history=("3",))

        with self.assertRaises(ValueError):
            WatchItemJourney(rotation_history=(0,))

        with self.assertRaises(ValueError):
            WatchItemJourney(rotation_history=(-2,))

        journey = WatchItemJourney()
        with self.assertRaises(TypeError):
            journey.record_rotation_entry("3")

        with self.assertRaises(ValueError):
            journey.record_rotation_entry(0)

    def test_journey_validates_watch_dates(self) -> None:
        with self.assertRaises(TypeError):
            WatchItemJourney(watch_dates=("2026-07-09",))

        journey = WatchItemJourney()
        with self.assertRaises(TypeError):
            journey.record_watch_date("2026-07-09")

    def test_journey_normalizes_winning_vote_in_init(self) -> None:
        journey = WatchItemJourney(winning_vote="  The Matrix  ")

        self.assertEqual(journey.winning_vote, "The Matrix")


if __name__ == "__main__":
    unittest.main()
