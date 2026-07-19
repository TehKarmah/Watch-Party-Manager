import unittest

from watch_party_manager.services.help_service import (
    GITHUB_DOCS_BASE_URL,
    HelpResponse,
    build_help_response,
    build_reference_links_text,
)


class HelpServiceTests(unittest.TestCase):
    def test_help_response_is_ephemeral(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertTrue(response.ephemeral)

    def test_member_help_is_a_single_message(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertEqual(len(response.messages), 1)

    def test_member_help_includes_member_commands(self) -> None:
        response = build_help_response(show_wash_crew=False)
        message = response.messages[0]

        self.assertIn("`/add` - Add a watch item by title or IMDb link.", message)
        self.assertIn("`/vote` - Cast or update your vote.", message)

    def test_member_help_hides_wash_crew_commands(self) -> None:
        response = build_help_response(show_wash_crew=False)
        message = response.messages[0]

        self.assertNotIn("`/database_add`", message)
        self.assertNotIn("`/diagnostics`", message)
        self.assertNotIn("`/backup`", message)
        self.assertNotIn("`/restore`", message)

    def test_member_help_links_to_reference_documentation(self) -> None:
        message = build_help_response(show_wash_crew=False).messages[0]

        self.assertIn("**Documentation & Reference**", message)
        self.assertIn(
            f"[Definitions & terminology]({GITHUB_DOCS_BASE_URL}/98-Glossary.md)",
            message,
        )
        self.assertIn(
            f"[Administration guide]({GITHUB_DOCS_BASE_URL}/05-Administration.md)",
            message,
        )
        self.assertIn(
            f"[Complete documentation]({GITHUB_DOCS_BASE_URL}/00-Table-of-Contents.md)",
            message,
        )

    def test_member_help_does_not_embed_glossary_definitions(self) -> None:
        message = build_help_response(show_wash_crew=False).messages[0]

        self.assertNotIn("**WASH Definitions**", message)
        self.assertNotIn("**Watch Item** -", message)
        self.assertNotIn("**Blind Vote** -", message)

    def test_wash_crew_help_uses_commands_then_reference_message(self) -> None:
        response = build_help_response(show_wash_crew=True)

        self.assertEqual(len(response.messages), 2)
        self.assertIn("**WASH Commands**", response.messages[0])
        self.assertIn("**Documentation & Reference**", response.messages[1])

    def test_wash_crew_help_includes_administrative_commands(self) -> None:
        message = build_help_response(show_wash_crew=True).messages[0]

        self.assertIn("`/database_add`", message)
        self.assertIn("`/diagnostics`", message)
        self.assertIn("`/backup`", message)
        self.assertIn("`/restore`", message)

    def test_wash_crew_help_links_to_reference_documentation(self) -> None:
        message = build_help_response(show_wash_crew=True).messages[1]

        self.assertIn("**Documentation & Reference**", message)
        self.assertIn("98-Glossary.md", message)
        self.assertIn("05-Administration.md", message)

    def test_reference_links_text_does_not_duplicate_commands(self) -> None:
        text = build_reference_links_text()

        self.assertNotIn("`/help`", text)
        self.assertNotIn("`/database_add`", text)

    def test_every_help_message_stays_within_discord_message_limit(self) -> None:
        member_response = build_help_response(show_wash_crew=False)
        crew_response = build_help_response(show_wash_crew=True)

        for message in member_response.messages + crew_response.messages:
            self.assertLessEqual(len(message), 2000)

    def test_help_messages_have_meaningful_headroom(self) -> None:
        member_message = build_help_response(show_wash_crew=False).messages[0]
        crew_message = build_help_response(show_wash_crew=True).messages[0]

        self.assertLess(len(member_message), 1900)
        self.assertLess(len(crew_message), 1900)

    def test_help_response_rejects_being_constructed_with_no_messages(self) -> None:
        with self.assertRaises(ValueError):
            HelpResponse(messages=(), ephemeral=True)


if __name__ == "__main__":
    unittest.main()
