import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    build_help_text,
    build_ping_text,
    format_datetime_for_display,
    is_wash_crew_member,
    parse_default_nominee_count,
    parse_guild_id,
    parse_vote_duration_days,
    parse_vote_nominee_count,
    parse_vote_visibility,
    parse_wash_crew_role_id,
    parse_watch_party_member_role_id,
)
from watch_party_manager.domain.vote import (
    DEFAULT_VOTE_CANDIDATE_COUNT,
    DEFAULT_VOTE_DURATION_DAYS,
    MAX_VOTE_CANDIDATE_COUNT,
    MAX_VOTE_DURATION_DAYS,
    MIN_VOTE_CANDIDATE_COUNT,
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
    def test_help_text_groups_and_lists_available_commands_for_wash_crew(self) -> None:
        help_text = build_help_text(show_admin=True)

        self.assertIn("**General**", help_text)
        self.assertIn("**Watch Items**", help_text)
        self.assertIn("**Voting**", help_text)
        self.assertIn("**WASH Crew: Suggestion Databases**", help_text)

        expected_commands = (
            "/help",
            "/ping",
            "/about",
            "/stats",
            "/add",
            "/list",
            "/remove",
            "/start_vote",
            "/vote_status",
            "/vote",
            "/database_add",
            "/database_list",
            "/database_remove",
            "/diagnostics",
        )
        for command in expected_commands:
            self.assertIn(command, help_text)

        self.assertLess(help_text.index("**General**"), help_text.index("**Watch Items**"))
        self.assertLess(help_text.index("**Watch Items**"), help_text.index("**Voting**"))
        self.assertLess(
            help_text.index("**Voting**"),
            help_text.index("**WASH Crew: Suggestion Databases**"),
        )

    def test_help_text_includes_structured_glossary(self) -> None:
        help_text = build_help_text(show_admin=False)

        self.assertIn("**WASH Definitions**", help_text)
        self.assertIn("**Watch Item**", help_text)
        self.assertIn("**Blind Vote**", help_text)

    def test_help_text_hides_wash_crew_commands_for_regular_members(self) -> None:
        help_text = build_help_text(show_admin=False)

        self.assertIn("**General**", help_text)
        self.assertIn("**Watch Items**", help_text)
        self.assertIn("**Voting**", help_text)
        self.assertNotIn("**WASH Crew: Suggestion Databases**", help_text)
        self.assertNotIn("/database_add", help_text)
        self.assertNotIn("/database_list", help_text)
        self.assertNotIn("/database_remove", help_text)
        self.assertNotIn("/diagnostics", help_text)
        self.assertIn("/stats", help_text)

    def test_ping_text_includes_latency_and_uptime(self) -> None:
        started_at = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        now = started_at + timedelta(days=1, hours=2, minutes=3, seconds=4)

        self.assertEqual(
            build_ping_text(42.6, started_at, now),
            "Pong.\nGateway latency: 43 ms\nUptime: 1d 2h 3m 4s",
        )

    def test_ping_text_handles_subminute_uptime(self) -> None:
        started_at = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        now = started_at + timedelta(seconds=9)

        self.assertTrue(build_ping_text(0.4, started_at, now).endswith("Uptime: 9s"))

    def test_ping_text_rejects_naive_datetimes(self) -> None:
        aware = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
        naive = datetime(2026, 7, 16, 10, 0)

        with self.assertRaises(ValueError):
            build_ping_text(10, naive, aware)
        with self.assertRaises(ValueError):
            build_ping_text(10, aware, naive)

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

    # --- Watch Party member role parsing -----------------------------------

    def test_parse_watch_party_member_role_id_returns_none_when_unset(self) -> None:
        self.assertIsNone(parse_watch_party_member_role_id(None))
        self.assertIsNone(parse_watch_party_member_role_id(""))

    def test_parse_watch_party_member_role_id_accepts_positive_integer(self) -> None:
        self.assertEqual(parse_watch_party_member_role_id("123"), 123)

    def test_parse_watch_party_member_role_id_rejects_non_integer(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_watch_party_member_role_id("abc")
        self.assertIn("WATCH_PARTY_MEMBER_ROLE_ID", str(ctx.exception))

    def test_parse_watch_party_member_role_id_rejects_non_positive(self) -> None:
        with self.assertRaises(ValueError):
            parse_watch_party_member_role_id("0")

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

    # --- Nominee count parsing -----------------------------------------------

    def test_parse_vote_nominee_count_returns_default_when_not_given(self) -> None:
        self.assertEqual(parse_vote_nominee_count(None), DEFAULT_VOTE_CANDIDATE_COUNT)

    def test_parse_vote_nominee_count_uses_a_custom_default_when_given(self) -> None:
        self.assertEqual(parse_vote_nominee_count(None, default=5), 5)

    def test_parse_vote_nominee_count_accepts_every_value_from_two_to_ten(self) -> None:
        for count in range(MIN_VOTE_CANDIDATE_COUNT, MAX_VOTE_CANDIDATE_COUNT + 1):
            with self.subTest(count=count):
                self.assertEqual(parse_vote_nominee_count(count), count)

    def test_parse_vote_nominee_count_rejects_one(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_nominee_count(1)

    def test_parse_vote_nominee_count_rejects_zero(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_nominee_count(0)

    def test_parse_vote_nominee_count_rejects_eleven(self) -> None:
        with self.assertRaises(ValueError):
            parse_vote_nominee_count(11)

    def test_parse_default_nominee_count_returns_default_when_unset(self) -> None:
        self.assertEqual(parse_default_nominee_count(None), DEFAULT_VOTE_CANDIDATE_COUNT)

    def test_parse_default_nominee_count_returns_default_for_empty_string(self) -> None:
        self.assertEqual(parse_default_nominee_count(""), DEFAULT_VOTE_CANDIDATE_COUNT)

    def test_parse_default_nominee_count_converts_a_valid_string(self) -> None:
        self.assertEqual(parse_default_nominee_count("5"), 5)

    def test_parse_default_nominee_count_rejects_non_numeric_strings(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            parse_default_nominee_count("not_a_number")
        self.assertIn("must be a valid integer", str(ctx.exception))

    def test_parse_default_nominee_count_rejects_a_value_below_the_minimum(self) -> None:
        with self.assertRaises(ValueError):
            parse_default_nominee_count("1")

    def test_parse_default_nominee_count_rejects_a_value_above_the_maximum(self) -> None:
        with self.assertRaises(ValueError):
            parse_default_nominee_count("11")

    def test_parse_default_nominee_count_accepts_the_boundaries(self) -> None:
        self.assertEqual(parse_default_nominee_count(str(MIN_VOTE_CANDIDATE_COUNT)), MIN_VOTE_CANDIDATE_COUNT)
        self.assertEqual(parse_default_nominee_count(str(MAX_VOTE_CANDIDATE_COUNT)), MAX_VOTE_CANDIDATE_COUNT)


if __name__ == "__main__":
    unittest.main()
