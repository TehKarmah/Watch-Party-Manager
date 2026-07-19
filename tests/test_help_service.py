import unittest

from watch_party_manager.services.help_service import HelpResponse, build_help_response


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

    def test_member_help_includes_glossary(self) -> None:
        response = build_help_response(show_wash_crew=False)
        message = response.messages[0]

        self.assertIn("**WASH Definitions**", message)
        self.assertIn("**Watch Item**", message)
        self.assertIn("**WASH Crew**", message)
        self.assertIn("**Blind Vote**", message)

    # --- WASH Crew: split into two messages -----------------------------------

    def test_wash_crew_help_is_two_messages(self) -> None:
        response = build_help_response(show_wash_crew=True)

        self.assertEqual(len(response.messages), 2)

    def test_wash_crew_receives_both_messages_in_commands_then_glossary_order(self) -> None:
        response = build_help_response(show_wash_crew=True)
        commands_message, glossary_message = response.messages

        self.assertIn("**WASH Commands**", commands_message)
        self.assertIn("**WASH Definitions**", glossary_message)
        # The command message should not itself carry glossary content, and
        # vice versa -- confirming these are genuinely separate messages
        # rather than one message with the other's content duplicated in.
        self.assertNotIn("**WASH Definitions**", commands_message)
        self.assertNotIn("**WASH Commands**", glossary_message)

    def test_wash_crew_help_includes_administrative_commands(self) -> None:
        response = build_help_response(show_wash_crew=True)
        commands_message = response.messages[0]

        self.assertIn("`/database_add`", commands_message)
        self.assertIn("`/diagnostics`", commands_message)

    def test_wash_crew_help_includes_backup_and_restore(self) -> None:
        response = build_help_response(show_wash_crew=True)
        commands_message = response.messages[0]

        self.assertIn("`/backup`", commands_message)
        self.assertIn("`/restore`", commands_message)

    def test_wash_crew_help_includes_glossary_in_the_second_message(self) -> None:
        response = build_help_response(show_wash_crew=True)
        glossary_message = response.messages[1]

        self.assertIn("**Watch Item**", glossary_message)
        self.assertIn("**Blind Vote**", glossary_message)

    def test_wash_crew_help_is_ephemeral(self) -> None:
        response = build_help_response(show_wash_crew=True)

        self.assertTrue(response.ephemeral)

    # --- Discord's message-length limit -----------------------------------------

    def test_every_help_message_stays_within_discord_message_limit(self) -> None:
        member_response = build_help_response(show_wash_crew=False)
        crew_response = build_help_response(show_wash_crew=True)

        for message in member_response.messages + crew_response.messages:
            self.assertLessEqual(len(message), 2000)

    def test_wash_crew_messages_each_have_meaningful_headroom(self) -> None:
        # Guards against the split becoming pointless if a single message
        # were still allowed to sit right at the limit -- each half should
        # have real room for future commands or glossary entries.
        crew_response = build_help_response(show_wash_crew=True)

        for message in crew_response.messages:
            self.assertLess(len(message), 1900)

    # --- HelpResponse itself ------------------------------------------------------

    def test_help_response_rejects_being_constructed_with_no_messages(self) -> None:
        with self.assertRaises(ValueError):
            HelpResponse(messages=(), ephemeral=True)


if __name__ == "__main__":
    unittest.main()
