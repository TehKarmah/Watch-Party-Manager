"""Tests for the shared duration-text parser (Requirement 3: Standardize
Duration Syntax) used by vote duration, reminder-before-close, and
/edit_vote's Shorten Vote/Extend Vote.
"""

import unittest

from watch_party_manager.services.duration_parser import parse_duration_to_minutes


class ParseDurationToMinutesTests(unittest.TestCase):
    def test_minutes_short_form(self) -> None:
        self.assertEqual(parse_duration_to_minutes("10m"), 10)

    def test_hours_short_form(self) -> None:
        self.assertEqual(parse_duration_to_minutes("1h"), 60)

    def test_days_short_form(self) -> None:
        self.assertEqual(parse_duration_to_minutes("1d"), 1440)

    def test_weeks_short_form(self) -> None:
        self.assertEqual(parse_duration_to_minutes("1w"), 10080)

    def test_minute_word_singular_and_plural(self) -> None:
        self.assertEqual(parse_duration_to_minutes("1 minute"), 1)
        self.assertEqual(parse_duration_to_minutes("10 minutes"), 10)

    def test_hour_word_singular_and_plural(self) -> None:
        self.assertEqual(parse_duration_to_minutes("1 hour"), 60)
        self.assertEqual(parse_duration_to_minutes("12 hours"), 720)

    def test_day_word_singular_and_plural(self) -> None:
        self.assertEqual(parse_duration_to_minutes("1 day"), 1440)
        self.assertEqual(parse_duration_to_minutes("3 days"), 4320)

    def test_week_word_singular_and_plural(self) -> None:
        self.assertEqual(parse_duration_to_minutes("1 week"), 10080)
        self.assertEqual(parse_duration_to_minutes("2 weeks"), 20160)

    def test_case_insensitive(self) -> None:
        self.assertEqual(parse_duration_to_minutes("1H"), 60)
        self.assertEqual(parse_duration_to_minutes("1 DAY"), 1440)

    def test_tolerates_surrounding_whitespace(self) -> None:
        self.assertEqual(parse_duration_to_minutes("  1h  "), 60)

    def test_no_space_and_space_both_accepted(self) -> None:
        self.assertEqual(parse_duration_to_minutes("30m"), 30)
        self.assertEqual(parse_duration_to_minutes("30 m"), 30)

    def test_rejects_a_bare_number_with_no_unit(self) -> None:
        with self.assertRaises(ValueError):
            parse_duration_to_minutes("7")

    def test_rejects_an_unrecognized_unit(self) -> None:
        with self.assertRaises(ValueError):
            parse_duration_to_minutes("7x")

    def test_rejects_zero(self) -> None:
        # Zero is syntactically a whole number, but never a meaningful
        # duration -- callers apply their own minimum bound on top of
        # this, so this parser only needs to accept it syntactically;
        # bounds tests live with each caller (vote duration, reminder).
        self.assertEqual(parse_duration_to_minutes("0m"), 0)

    def test_rejects_a_negative_number(self) -> None:
        with self.assertRaises(ValueError):
            parse_duration_to_minutes("-5m")

    def test_rejects_empty_text(self) -> None:
        with self.assertRaises(ValueError):
            parse_duration_to_minutes("")

    def test_rejects_none(self) -> None:
        with self.assertRaises(ValueError):
            parse_duration_to_minutes(None)


if __name__ == "__main__":
    unittest.main()
