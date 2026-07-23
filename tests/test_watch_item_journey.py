import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.domain import WatchItemJourney


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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

    def test_rejected_by_discord_user_ids_defaults_to_empty(self) -> None:
        journey = WatchItemJourney()

        self.assertEqual(journey.rejected_by_discord_user_ids, ())

    def test_rejected_by_discord_user_ids_deduplicates_on_init(self) -> None:
        journey = WatchItemJourney(rejected_by_discord_user_ids=(1, 2, 1))

        self.assertEqual(journey.rejected_by_discord_user_ids, (1, 2))

    def test_rejected_by_discord_user_ids_validates_positive_integers(self) -> None:
        with self.assertRaises(ValueError):
            WatchItemJourney(rejected_by_discord_user_ids=(0,))

        with self.assertRaises(ValueError):
            WatchItemJourney(rejected_by_discord_user_ids=(-1,))


class RecordRejectionTests(unittest.TestCase):
    def test_records_a_new_rejection(self) -> None:
        journey = WatchItemJourney()

        recorded = journey.record_rejection(1)

        self.assertTrue(recorded)
        self.assertEqual(journey.rejected_by_discord_user_ids, (1,))

    def test_records_multiple_distinct_rejections(self) -> None:
        journey = WatchItemJourney()

        journey.record_rejection(1)
        journey.record_rejection(2)

        self.assertEqual(journey.rejected_by_discord_user_ids, (1, 2))

    def test_duplicate_rejection_from_the_same_member_is_a_no_op(self) -> None:
        journey = WatchItemJourney()
        journey.record_rejection(1)

        recorded_again = journey.record_rejection(1)

        self.assertFalse(recorded_again)
        self.assertEqual(journey.rejected_by_discord_user_ids, (1,))

    def test_rejects_a_non_positive_discord_user_id(self) -> None:
        journey = WatchItemJourney()

        with self.assertRaises(ValueError):
            journey.record_rejection(0)


class RemoveRejectionTests(unittest.TestCase):
    def test_removes_an_existing_rejection(self) -> None:
        journey = WatchItemJourney()
        journey.record_rejection(1)

        removed = journey.remove_rejection(1)

        self.assertTrue(removed)
        self.assertEqual(journey.rejected_by_discord_user_ids, ())

    def test_removing_a_rejection_that_does_not_exist_is_a_no_op(self) -> None:
        journey = WatchItemJourney()

        removed = journey.remove_rejection(1)

        self.assertFalse(removed)

    def test_removing_one_rejection_preserves_others(self) -> None:
        journey = WatchItemJourney()
        journey.record_rejection(1)
        journey.record_rejection(2)

        journey.remove_rejection(1)

        self.assertEqual(journey.rejected_by_discord_user_ids, (2,))


class RecordRotationEntryIdempotencyTests(unittest.TestCase):
    def test_recording_the_same_rotation_twice_does_not_duplicate_it(self) -> None:
        journey = WatchItemJourney()
        journey.record_rotation_entry(3)

        journey.record_rotation_entry(3)

        self.assertEqual(journey.rotation_history, (3,))

    def test_recording_a_different_rotation_appends_it(self) -> None:
        journey = WatchItemJourney()
        journey.record_rotation_entry(3)

        journey.record_rotation_entry(4)

        self.assertEqual(journey.rotation_history, (3, 4))


class RetirementTests(unittest.TestCase):
    def test_defaults_have_no_retirement_recorded(self) -> None:
        journey = WatchItemJourney()

        self.assertIsNone(journey.retired_at)
        self.assertIsNone(journey.retirement_reason)
        self.assertIsNone(journey.retired_from_rotation_id)
        self.assertIsNone(journey.retired_from_vote_round_id)

    def test_record_retirement_sets_all_fields(self) -> None:
        journey = WatchItemJourney()
        retired_at = utc_now()

        journey.record_retirement(retired_at, "rejection_threshold_reached", rotation_id=5, vote_round_id=2)

        self.assertEqual(journey.retired_at, retired_at)
        self.assertEqual(journey.retirement_reason, "rejection_threshold_reached")
        self.assertEqual(journey.retired_from_rotation_id, 5)
        self.assertEqual(journey.retired_from_vote_round_id, 2)

    def test_record_retirement_works_without_optional_context(self) -> None:
        journey = WatchItemJourney()

        journey.record_retirement(utc_now(), "rejection_threshold_reached")

        self.assertIsNone(journey.retired_from_rotation_id)
        self.assertIsNone(journey.retired_from_vote_round_id)

    def test_record_retirement_rejects_a_naive_datetime(self) -> None:
        journey = WatchItemJourney()

        with self.assertRaises(ValueError):
            journey.record_retirement(datetime.now(), "reason")

    def test_record_retirement_rejects_a_non_positive_rotation_id(self) -> None:
        journey = WatchItemJourney()

        with self.assertRaises(ValueError):
            journey.record_retirement(utc_now(), "reason", rotation_id=0)

    def test_record_retirement_rejects_a_non_positive_vote_round_id(self) -> None:
        journey = WatchItemJourney()

        with self.assertRaises(ValueError):
            journey.record_retirement(utc_now(), "reason", vote_round_id=0)

    def test_constructing_with_a_naive_retired_at_raises(self) -> None:
        with self.assertRaises(ValueError):
            WatchItemJourney(retired_at=datetime.now())


if __name__ == "__main__":
    unittest.main()
