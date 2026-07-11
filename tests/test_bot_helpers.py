import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import build_help_text, build_version_text, parse_guild_id


class BotHelperTests(unittest.TestCase):
    def test_help_text_lists_available_commands(self) -> None:
        help_text = build_help_text()

        self.assertIn("/ping", help_text)
        self.assertIn("/version", help_text)
        self.assertIn("/help", help_text)
        self.assertIn("/suggest", help_text)
        self.assertIn("/suggestions", help_text)
        self.assertIn("/remove_suggestion", help_text)

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


if __name__ == "__main__":
    unittest.main()
