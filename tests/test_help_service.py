import unittest

from watch_party_manager.services.help_service import (
    EXPANDED_HELP_URL,
    HelpResponse,
    build_help_response,
    build_expanded_help_link_text,
)


class HelpServiceTests(unittest.TestCase):
    def test_help_response_is_ephemeral(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertTrue(response.ephemeral)

    def test_everyone_help_is_a_single_message(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertEqual(len(response.messages), 1)

    def test_everyone_help_hides_member_and_wash_crew_commands(self) -> None:
        response = build_help_response(show_wash_crew=False)
        message = response.messages[0]

        self.assertNotIn("`/add`", message)
        self.assertNotIn("`/database_add`", message)
        self.assertNotIn("`/diagnostics`", message)
        self.assertNotIn("`/backup`", message)
        self.assertNotIn("`/restore`", message)
        self.assertNotIn("`/setup`", message)
        self.assertNotIn("`/config`", message)

    def test_everyone_help_includes_join_watch_party(self) -> None:
        # FR-030: /join_watch_party is public, alongside /help and /about.
        message = build_help_response(show_wash_crew=False).messages[0]

        self.assertIn("`/join_watch_party`", message)
        self.assertIn("`/help`", message)
        self.assertIn("`/about`", message)

    def test_everyone_help_links_to_expanded_documentation(self) -> None:
        message = build_help_response(show_wash_crew=False).messages[0]

        self.assertIn("**Expanded Help Documentation**", message)
        self.assertIn(
            f"[Open the WASH help guide on GitHub]({EXPANDED_HELP_URL})",
            message,
        )
        self.assertNotIn("Definitions & terminology", message)
        self.assertNotIn("Administration guide", message)
        self.assertNotIn("Complete documentation", message)

    def test_everyone_help_does_not_embed_glossary_definitions(self) -> None:
        message = build_help_response(show_wash_crew=False).messages[0]

        self.assertNotIn("**WASH Definitions**", message)
        self.assertNotIn("**Watch Item** -", message)
        self.assertNotIn("**Blind Vote** -", message)

    def test_watch_party_member_help_uses_commands_then_reference_message(self) -> None:
        response = build_help_response(show_wash_crew=False, show_watch_party_member=True)

        self.assertEqual(len(response.messages), 2)
        self.assertIn("**WASH Commands**", response.messages[0])
        self.assertIn("**Expanded Help Documentation**", response.messages[1])

    def test_watch_party_member_help_includes_add(self) -> None:
        message = build_help_response(show_wash_crew=False, show_watch_party_member=True).messages[0]

        self.assertIn("`/add` - Add a watch item by title or IMDb link.", message)

    def test_watch_party_member_help_includes_list(self) -> None:
        # FR-033A: Watch Party members gain /list (view-only, never
        # public) alongside /add over the "everyone" tier.
        message = build_help_response(show_wash_crew=False, show_watch_party_member=True).messages[0]

        self.assertIn("`/list`", message)

    def test_watch_party_member_help_hides_wash_crew_and_other_member_commands(self) -> None:
        message = build_help_response(show_wash_crew=False, show_watch_party_member=True).messages[0]

        self.assertNotIn("`/remove`", message)
        self.assertNotIn("`/edit_suggestion`", message)
        self.assertNotIn("`/vote_status`", message)
        self.assertNotIn("`/watch_party_status`", message)
        self.assertNotIn("`/stats`", message)
        self.assertNotIn("`/database_add`", message)
        self.assertNotIn("`/diagnostics`", message)
        self.assertNotIn("`/backup`", message)
        self.assertNotIn("`/restore`", message)
        self.assertNotIn("`/setup`", message)
        self.assertNotIn("`/config`", message)
        self.assertNotIn("`/start_vote`", message)
        self.assertNotIn("`/edit_vote`", message)

    def test_wash_crew_help_uses_commands_then_reference_message(self) -> None:
        response = build_help_response(show_wash_crew=True)

        self.assertEqual(len(response.messages), 2)
        self.assertIn("**WASH Commands**", response.messages[0])
        self.assertIn("**Expanded Help Documentation**", response.messages[1])

    def test_wash_crew_help_includes_administrative_commands(self) -> None:
        message = build_help_response(show_wash_crew=True).messages[0]

        self.assertIn("`/database_add`", message)
        self.assertIn("`/diagnostics`", message)
        self.assertIn("`/backup`", message)
        self.assertIn("`/restore`", message)
        self.assertIn("`/setup`", message)
        self.assertIn("`/config`", message)

    def test_wash_crew_help_also_includes_member_commands(self) -> None:
        # WASH Crew inherits every Watch Party member capability.
        message = build_help_response(show_wash_crew=True).messages[0]

        self.assertIn("`/add`", message)
        self.assertIn("`/list`", message)

    def test_wash_crew_help_never_mentions_vote_command(self) -> None:
        message = build_help_response(show_wash_crew=True).messages[0]

        self.assertNotIn("`/vote`", message)

    def test_wash_crew_help_links_to_expanded_documentation(self) -> None:
        message = build_help_response(show_wash_crew=True).messages[1]

        self.assertIn("**Expanded Help Documentation**", message)
        self.assertIn(EXPANDED_HELP_URL, message)
        self.assertNotIn("98-Glossary.md", message)
        self.assertNotIn("05-Administration.md", message)

    def test_expanded_help_link_text_does_not_duplicate_commands(self) -> None:
        text = build_expanded_help_link_text()

        self.assertNotIn("`/help`", text)
        self.assertNotIn("`/database_add`", text)

    def test_every_help_message_stays_within_discord_message_limit(self) -> None:
        everyone_response = build_help_response(show_wash_crew=False)
        member_response = build_help_response(show_wash_crew=False, show_watch_party_member=True)
        crew_response = build_help_response(show_wash_crew=True)

        for message in everyone_response.messages + member_response.messages + crew_response.messages:
            self.assertLessEqual(len(message), 2000)

    def test_help_messages_have_meaningful_headroom(self) -> None:
        everyone_message = build_help_response(show_wash_crew=False).messages[0]
        member_message = build_help_response(show_wash_crew=False, show_watch_party_member=True).messages[0]
        crew_message = build_help_response(show_wash_crew=True).messages[0]

        self.assertLess(len(everyone_message), 1900)
        self.assertLess(len(member_message), 1900)
        self.assertLess(len(crew_message), 1900)

    def test_help_response_rejects_being_constructed_with_no_messages(self) -> None:
        with self.assertRaises(ValueError):
            HelpResponse(messages=(), ephemeral=True)


if __name__ == "__main__":
    unittest.main()
