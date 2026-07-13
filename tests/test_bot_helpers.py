import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_help_text,
    format_datetime_for_display,
    build_version_text,
    is_wash_crew_member,
    parse_guild_id,
    parse_vote_duration_days,
    parse_vote_visibility,
    parse_wash_crew_role_id,
)
from watch_party_manager.domain.vote import (
    DEFAULT_VOTE_DURATION_DAYS,
    MAX_VOTE_DURATION_DAYS,
    MIN_VOTE_DURATION_DAYS,
    VoteVisibility,
)


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class BotHelperTests(unittest.TestCase):
    def test_help_text_lists_available_commands(self) -> None:
        help_text = build_help_text()

        self.assertIn("/ping", help_text)
        self.assertIn("/version", help_text)
        self.assertIn("/help", help_text)
        self.assertIn("/add", help_text)
        self.assertIn("/list", help_text)
        self.assertIn("/remove", help_text)
        self.assertIn("/start_vote", help_text)
        self.assertIn("/vote_status", help_text)

    def test_version_text_uses_the_provided_version(self) -> None:
        self.assertEqual(build_version_text("0.2.0"), "Watch Party Manager version 0.2.0")

    def test_parse_guild_id_returns_none_when_not_provided(self) -> None:
        self.assertIsNone(parse_guild_id(None))
        self.assertIsNone(parse_guild_id(""))

    def test_parse_guild_id_converts_valid_string_to_integer(self) -> None:
        self.assertEqual(parse_guild_id("123456789"), 123456789)

    def test_parse_guild_id_rejects_non_numeric_strings(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_guild_id("not_a_number")
        self.assertIn("must be a valid integer", str(ctx.exception))

    def test_parse_guild_id_rejects_zero(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_guild_id("0")
        self.assertIn("must be a positive integer", str(ctx.exception))

    def test_parse_guild_id_rejects_negative_numbers(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_guild_id("-123")
        self.assertIn("must be a positive integer", str(ctx.exception))

    # --- WASH Crew role configuration --------------------------------------

    def test_parse_wash_crew_role_id_returns_none_when_not_provided(self) -> None:
        self.assertIsNone(parse_wash_crew_role_id(None))
        self.assertIsNone(parse_wash_crew_role_id(""))

    def test_parse_wash_crew_role_id_converts_valid_string_to_integer(self) -> None:
        self.assertEqual(parse_wash_crew_role_id("987654321"), 987654321)

    def test_parse_wash_crew_role_id_rejects_non_numeric_strings(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_wash_crew_role_id("not_a_number")
        self.assertIn("must be a valid integer", str(ctx.exception))

    def test_parse_wash_crew_role_id_rejects_zero(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_wash_crew_role_id("0")
        self.assertIn("must be a positive integer", str(ctx.exception))

    def test_parse_wash_crew_role_id_rejects_negative_numbers(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_wash_crew_role_id("-5")
        self.assertIn("must be a positive integer", str(ctx.exception))

    def test_is_wash_crew_member_fails_closed_when_unconfigured(self) -> None:
        member = FakeMember(roles=[])
        self.assertFalse(is_wash_crew_member(member, wash_crew_role_id=None))

    def test_is_wash_crew_member_fails_closed_even_with_roles_when_unconfigured(self) -> None:
        # A member's roles shouldn't matter at all when nothing is
        # configured to check them against.
        member = FakeMember(roles=[FakeRole(111), FakeRole(222)])
        self.assertFalse(is_wash_crew_member(member, wash_crew_role_id=None))

    def test_is_wash_crew_member_true_when_member_has_the_role(self) -> None:
        member = FakeMember(roles=[FakeRole(111), FakeRole(222)])
        self.assertTrue(is_wash_crew_member(member, wash_crew_role_id=222))

    def test_is_wash_crew_member_false_when_member_lacks_the_role(self) -> None:
        member = FakeMember(roles=[FakeRole(111)])
        self.assertFalse(is_wash_crew_member(member, wash_crew_role_id=222))

    def test_is_wash_crew_member_false_when_member_has_no_roles(self) -> None:
        member = FakeMember(roles=[])
        self.assertFalse(is_wash_crew_member(member, wash_crew_role_id=222))

    # --- Vote visibility parsing --------------------------------------------

    def test_parse_vote_visibility_accepts_blind(self) -> None:
        self.assertEqual(parse_vote_visibility("blind"), VoteVisibility.BLIND)

    def test_parse_vote_visibility_accepts_visible(self) -> None:
        self.assertEqual(parse_vote_visibility("visible"), VoteVisibility.VISIBLE)

    def test_parse_vote_visibility_is_case_insensitive(self) -> None:
        self.assertEqual(parse_vote_visibility("BLIND"), VoteVisibility.BLIND)

    def test_parse_vote_visibility_ignores_surrounding_whitespace(self) -> None:
        self.assertEqual(parse_vote_visibility("  visible  "), VoteVisibility.VISIBLE)

    def test_parse_vote_visibility_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_vote_visibility("sneaky")
        self.assertIn("blind", str(ctx.exception).lower())
        self.assertIn("visible", str(ctx.exception).lower())

    # --- Vote duration validation --------------------------------------------

    def test_parse_vote_duration_days_returns_default_when_not_given(self) -> None:
        self.assertEqual(parse_vote_duration_days(None), DEFAULT_VOTE_DURATION_DAYS)

    def test_parse_vote_duration_days_accepts_the_minimum_boundary(self) -> None:
        self.assertEqual(parse_vote_duration_days(MIN_VOTE_DURATION_DAYS), MIN_VOTE_DURATION_DAYS)
        self.assertEqual(parse_vote_duration_days(1), 1)

    def test_parse_vote_duration_days_accepts_the_maximum_boundary(self) -> None:
        self.assertEqual(parse_vote_duration_days(MAX_VOTE_DURATION_DAYS), MAX_VOTE_DURATION_DAYS)
        self.assertEqual(parse_vote_duration_days(30), 30)

    def test_parse_vote_duration_days_accepts_a_value_in_the_middle(self) -> None:
        self.assertEqual(parse_vote_duration_days(14), 14)

    def test_parse_vote_duration_days_rejects_zero(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_vote_duration_days(0)
        self.assertIn("between", str(ctx.exception))

    def test_parse_vote_duration_days_rejects_negative_values(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_duration_days(-1)

    def test_parse_vote_duration_days_rejects_values_above_the_maximum(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_duration_days(31)

    # --- Discord timestamp formatting ---------------------------------------

    def test_format_datetime_for_display_returns_fallback_for_none(self) -> None:
        self.assertEqual(format_datetime_for_display(None), "No deadline set")

    def test_format_datetime_for_display_uses_full_and_relative_discord_codes(self) -> None:
        value = datetime(2026, 7, 19, 18, 52, tzinfo=timezone.utc)
        timestamp = int(value.timestamp())

        self.assertEqual(
            format_datetime_for_display(value),
            f"<t:{timestamp}:F> (<t:{timestamp}:R>)",
        )

    def test_format_datetime_for_display_preserves_the_same_instant_across_timezones(self) -> None:
        utc_value = datetime(2026, 7, 19, 18, 52, tzinfo=timezone.utc)
        local_value = utc_value.astimezone(timezone(timedelta(hours=-7)))

        self.assertEqual(
            format_datetime_for_display(local_value),
            format_datetime_for_display(utc_value),
        )

    def test_format_datetime_for_display_rejects_naive_datetime(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            format_datetime_for_display(datetime(2026, 7, 19, 18, 52))

        self.assertIn("timezone-aware", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
