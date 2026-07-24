import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.services.duration_formatter import format_duration_hours


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


if __name__ == "__main__":
    unittest.main()
