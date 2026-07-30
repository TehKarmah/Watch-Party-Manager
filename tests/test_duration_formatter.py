import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.services.duration_formatter import (
    format_duration_hours,
    format_duration_minutes_compact,
)


class FormatDurationHoursTests(unittest.TestCase):
    def test_one_hour_is_singular(self) -> None:
        self.assertEqual(format_duration_hours(1), "1 hour")

    def test_four_hours(self) -> None:
        self.assertEqual(format_duration_hours(4), "4 hours")

    def test_twelve_hours(self) -> None:
        self.assertEqual(format_duration_hours(12), "12 hours")

    def test_twenty_three_hours_is_not_rounded_up_to_a_day(self) -> None:
        self.assertEqual(format_duration_hours(23), "23 hours")

    def test_twenty_four_hours_is_shown_as_one_day(self) -> None:
        # The single consistent convention: whole days when evenly
        # divisible by 24, never the more awkward "24 hours".
        self.assertEqual(format_duration_hours(24), "1 day")

    def test_seventy_two_hours_is_shown_as_three_days(self) -> None:
        self.assertEqual(format_duration_hours(72), "3 days")

    def test_one_hundred_sixty_eight_hours_is_shown_as_seven_days(self) -> None:
        self.assertEqual(format_duration_hours(168), "7 days")

    def test_seven_hundred_twenty_hours_is_shown_as_thirty_days(self) -> None:
        self.assertEqual(format_duration_hours(720), "30 days")

    def test_twenty_five_hours_is_not_evenly_divisible_and_stays_in_hours(self) -> None:
        self.assertEqual(format_duration_hours(25), "25 hours")


class FormatDurationMinutesCompactTests(unittest.TestCase):
    """Release Polish (Batch): the compact WASH input syntax used to
    prefill editable duration fields (Voting Defaults, Reminder Defaults),
    matching each field's own "10m, 1h, 7d" example wording (Duration UX
    Standard).
    """

    def test_minutes_below_an_hour_stay_in_minutes(self) -> None:
        self.assertEqual(format_duration_minutes_compact(10), "10m")
        self.assertEqual(format_duration_minutes_compact(1), "1m")

    def test_a_whole_hour_is_shown_in_hours(self) -> None:
        self.assertEqual(format_duration_minutes_compact(60), "1h")
        self.assertEqual(format_duration_minutes_compact(120), "2h")

    def test_a_partial_hour_stays_in_minutes(self) -> None:
        self.assertEqual(format_duration_minutes_compact(90), "90m")

    def test_a_whole_day_is_shown_in_days(self) -> None:
        self.assertEqual(format_duration_minutes_compact(24 * 60), "1d")
        self.assertEqual(format_duration_minutes_compact(3 * 24 * 60), "3d")

    def test_a_whole_week_is_shown_in_weeks(self) -> None:
        self.assertEqual(format_duration_minutes_compact(7 * 24 * 60), "1w")
        self.assertEqual(format_duration_minutes_compact(14 * 24 * 60), "2w")

    def test_picks_the_largest_whole_unit_it_evenly_divides_into(self) -> None:
        # 1 day's worth of minutes could also be expressed in hours, but
        # the largest evenly-dividing unit (days) is preferred.
        self.assertEqual(format_duration_minutes_compact(24 * 60), "1d")


if __name__ == "__main__":
    unittest.main()
