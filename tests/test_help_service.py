import unittest

from watch_party_manager.services.help_service import build_help_response


class HelpServiceTests(unittest.TestCase):
    def test_help_response_is_ephemeral(self) -> None:
        _, ephemeral = build_help_response(show_wash_crew=False)

        self.assertTrue(ephemeral)

    def test_member_help_includes_member_commands(self) -> None:
        message, _ = build_help_response(show_wash_crew=False)

        self.assertIn("`/add` - Add a watch item by title or IMDb link.", message)
        self.assertIn("`/vote` - Cast or update your vote.", message)

    def test_member_help_hides_wash_crew_commands(self) -> None:
        message, _ = build_help_response(show_wash_crew=False)

        self.assertNotIn("`/database_add`", message)
        self.assertNotIn("`/diagnostics`", message)

    def test_wash_crew_help_includes_administrative_commands(self) -> None:
        message, _ = build_help_response(show_wash_crew=True)

        self.assertIn("`/database_add`", message)
        self.assertIn("`/diagnostics`", message)

    def test_help_response_includes_glossary(self) -> None:
        message, _ = build_help_response(show_wash_crew=False)

        self.assertIn("**WASH Definitions**", message)
        self.assertIn("**Watch Item**", message)
        self.assertIn("**WASH Crew**", message)
        self.assertIn("**Blind Vote**", message)

    def test_help_response_stays_within_discord_message_limit(self) -> None:
        member_message, _ = build_help_response(show_wash_crew=False)
        crew_message, _ = build_help_response(show_wash_crew=True)

        self.assertLessEqual(len(member_message), 2000)
        self.assertLessEqual(len(crew_message), 2000)


if __name__ == "__main__":
    unittest.main()
