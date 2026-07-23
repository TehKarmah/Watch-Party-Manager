import unittest

from watch_party_manager.services.help_service import (
    COMMANDS_REFERENCE_URL,
    EXPANDED_HELP_URL,
    HelpResponse,
    build_help_response,
)


class HelpServiceTests(unittest.TestCase):
    def test_help_response_is_ephemeral(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertTrue(response.ephemeral)

    def test_everyone_help_hides_member_and_wash_crew_commands(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertNotIn("`/add`", response.command_text)
        self.assertNotIn("`/database_add`", response.command_text)
        self.assertNotIn("`/diagnostics`", response.command_text)
        self.assertNotIn("`/backup`", response.command_text)
        self.assertNotIn("`/restore`", response.command_text)
        self.assertNotIn("`/setup`", response.command_text)
        self.assertNotIn("`/config`", response.command_text)

    def test_everyone_help_includes_join_watch_party(self) -> None:
        # FR-030: /join_watch_party is public, alongside /help and /about.
        response = build_help_response(show_wash_crew=False)

        self.assertIn("`/join_watch_party`", response.command_text)
        self.assertIn("`/help`", response.command_text)
        self.assertIn("`/about`", response.command_text)

    def test_reference_points_at_the_commands_reference_document(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertEqual("Commands Reference", response.reference_title)
        self.assertEqual(COMMANDS_REFERENCE_URL, response.reference_url)
        self.assertIn("10-Command-Reference.md", response.reference_url)

    def test_reference_cross_links_the_expanded_help_guide(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertIn(EXPANDED_HELP_URL, response.reference_description)

    def test_command_text_does_not_embed_glossary_definitions(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertNotIn("**WASH Definitions**", response.command_text)
        self.assertNotIn("**Watch Item** -", response.command_text)
        self.assertNotIn("**Blind Vote** -", response.command_text)

    def test_command_text_does_not_duplicate_the_reference_link(self) -> None:
        response = build_help_response(show_wash_crew=False)

        self.assertNotIn(EXPANDED_HELP_URL, response.command_text)
        self.assertNotIn(COMMANDS_REFERENCE_URL, response.command_text)

    def test_watch_party_member_help_includes_add(self) -> None:
        response = build_help_response(show_wash_crew=False, show_watch_party_member=True)

        self.assertIn("`/add` - Add a watch item by title or IMDb link.", response.command_text)

    def test_watch_party_member_help_includes_list(self) -> None:
        # FR-033A: Watch Party members gain /list (view-only, never
        # public) alongside /add over the "everyone" tier.
        response = build_help_response(show_wash_crew=False, show_watch_party_member=True)

        self.assertIn("`/list`", response.command_text)

    def test_watch_party_member_help_includes_stats(self) -> None:
        # FR-034: Watch Party members gain /stats (privacy-scoped --
        # ephemeral by default, public posting requires WASH Crew except
        # for a member's own statistics) alongside /add and /list.
        response = build_help_response(show_wash_crew=False, show_watch_party_member=True)

        self.assertIn("`/stats`", response.command_text)

    def test_watch_party_member_help_hides_wash_crew_and_other_member_commands(self) -> None:
        response = build_help_response(show_wash_crew=False, show_watch_party_member=True)

        self.assertNotIn("`/remove`", response.command_text)
        self.assertNotIn("`/edit_suggestion`", response.command_text)
        self.assertNotIn("`/vote_status`", response.command_text)
        self.assertNotIn("`/watch_party_status`", response.command_text)
        self.assertNotIn("`/database_add`", response.command_text)
        self.assertNotIn("`/diagnostics`", response.command_text)
        self.assertNotIn("`/backup`", response.command_text)
        self.assertNotIn("`/restore`", response.command_text)
        self.assertNotIn("`/setup`", response.command_text)
        self.assertNotIn("`/config`", response.command_text)
        self.assertNotIn("`/start_vote`", response.command_text)
        self.assertNotIn("`/edit_vote`", response.command_text)

    def test_wash_crew_help_includes_administrative_commands(self) -> None:
        response = build_help_response(show_wash_crew=True)

        self.assertIn("`/database_add`", response.command_text)
        self.assertIn("`/diagnostics`", response.command_text)
        self.assertIn("`/backup`", response.command_text)
        self.assertIn("`/restore`", response.command_text)
        self.assertIn("`/setup`", response.command_text)
        self.assertIn("`/config`", response.command_text)

    def test_wash_crew_help_also_includes_member_commands(self) -> None:
        # WASH Crew inherits every Watch Party member capability.
        response = build_help_response(show_wash_crew=True)

        self.assertIn("`/add`", response.command_text)
        self.assertIn("`/list`", response.command_text)

    def test_wash_crew_help_never_mentions_vote_command(self) -> None:
        response = build_help_response(show_wash_crew=True)

        self.assertNotIn("`/vote`", response.command_text)

    def test_command_text_stays_within_discord_message_limit(self) -> None:
        everyone_response = build_help_response(show_wash_crew=False)
        member_response = build_help_response(show_wash_crew=False, show_watch_party_member=True)
        crew_response = build_help_response(show_wash_crew=True)

        for response in (everyone_response, member_response, crew_response):
            self.assertLessEqual(len(response.command_text), 2000)

    def test_command_text_has_meaningful_headroom(self) -> None:
        everyone_response = build_help_response(show_wash_crew=False)
        member_response = build_help_response(show_wash_crew=False, show_watch_party_member=True)
        crew_response = build_help_response(show_wash_crew=True)

        for response in (everyone_response, member_response, crew_response):
            self.assertLess(len(response.command_text), 1900)

    def test_reference_description_stays_within_discord_embed_field_limits(self) -> None:
        response = build_help_response(show_wash_crew=True)

        self.assertLessEqual(len(response.reference_title), 256)
        self.assertLessEqual(len(response.reference_description), 4096)

    def test_help_response_rejects_being_constructed_with_empty_command_text(self) -> None:
        with self.assertRaises(ValueError):
            HelpResponse(
                command_text="",
                reference_title="Commands Reference",
                reference_description="description",
                reference_url=COMMANDS_REFERENCE_URL,
                ephemeral=True,
            )

    def test_help_response_rejects_being_constructed_with_no_reference_url(self) -> None:
        with self.assertRaises(ValueError):
            HelpResponse(
                command_text="command text",
                reference_title="Commands Reference",
                reference_description="description",
                reference_url="",
                ephemeral=True,
            )


if __name__ == "__main__":
    unittest.main()
