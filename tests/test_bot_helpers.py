import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import build_help_text, build_version_text


class BotHelperTests(unittest.TestCase):
    def test_help_text_lists_available_commands(self) -> None:
        help_text = build_help_text()

        self.assertIn("/ping", help_text)
        self.assertIn("/version", help_text)
        self.assertIn("/help", help_text)

    def test_version_text_uses_the_provided_version(self) -> None:
        self.assertEqual(build_version_text("0.2.0"), "Watch Party Manager version 0.2.0")


if __name__ == "__main__":
    unittest.main()
