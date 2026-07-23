import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.help_registry import (
    COMMAND_HELP,
    CommandHelp,
    HelpAudience,
    build_command_help_text,
    command_sections,
)


class HelpRegistryTests(unittest.TestCase):
    def test_command_registry_has_unique_names(self) -> None:
        names = [entry.name for entry in COMMAND_HELP]
        self.assertEqual(len(names), len(set(names)))

    def test_command_help_validates_command_prefix(self) -> None:
        with self.assertRaisesRegex(ValueError, "begin with"):
            CommandHelp("help", "Show help.", "General")

    def test_everyone_sections_exclude_watch_party_member_and_wash_crew_entries(self) -> None:
        sections = command_sections(show_wash_crew=False)
        commands = [entry.name for _, entries in sections for entry in entries]
        self.assertIn("/help", commands)
        self.assertIn("/about", commands)
        self.assertNotIn("/add", commands)
        self.assertNotIn("/list", commands)
        self.assertNotIn("/database_add", commands)
        self.assertNotIn("/diagnostics", commands)
        self.assertNotIn("/setup", commands)
        self.assertNotIn("/config", commands)

    # --- FR-030: /join_watch_party is visible to everyone ------------------------

    def test_join_watch_party_is_visible_to_everyone(self) -> None:
        sections = command_sections(show_wash_crew=False)
        commands = [entry.name for _, entries in sections for entry in entries]
        self.assertIn("/join_watch_party", commands)

    def test_join_watch_party_has_everyone_audience(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        self.assertIs(entries["/join_watch_party"].audience, HelpAudience.EVERYONE)

    def test_join_watch_party_is_visible_to_every_tier(self) -> None:
        for show_wash_crew, show_watch_party_member in ((False, False), (False, True), (True, False)):
            sections = command_sections(
                show_wash_crew=show_wash_crew, show_watch_party_member=show_watch_party_member
            )
            commands = [entry.name for _, entries in sections for entry in entries]
            self.assertIn("/join_watch_party", commands)

    def test_watch_party_member_sections_add_only_add_list_and_stats(self) -> None:
        # FR-033A: Watch Party members gain /add and /list (view-only,
        # never public -- enforced by permission checks, not by hiding
        # the command) over the "everyone" tier. FR-034 additionally
        # gives them /stats (privacy-scoped the same way -- see
        # StatsType/handle_stats). Every other previously member-facing
        # command (remove, vote_status, watch_party_status, diagnostics)
        # remains WASH Crew only.
        sections = command_sections(show_wash_crew=False, show_watch_party_member=True)
        commands = [entry.name for _, entries in sections for entry in entries]
        self.assertIn("/help", commands)
        self.assertIn("/about", commands)
        self.assertIn("/add", commands)
        self.assertIn("/list", commands)
        self.assertIn("/stats", commands)
        self.assertNotIn("/remove", commands)
        self.assertNotIn("/edit_suggestion", commands)
        self.assertNotIn("/vote_status", commands)
        self.assertNotIn("/watch_party_status", commands)
        self.assertNotIn("/database_add", commands)
        self.assertNotIn("/diagnostics", commands)
        self.assertNotIn("/setup", commands)
        self.assertNotIn("/config", commands)

    def test_crew_sections_include_administrative_entries(self) -> None:
        sections = command_sections(show_wash_crew=True)
        commands = [entry.name for _, entries in sections for entry in entries]
        self.assertIn("/database_add", commands)
        self.assertIn("/diagnostics", commands)
        self.assertIn("/repair_suggestions", commands)
        self.assertIn("/setup", commands)
        self.assertIn("/config", commands)

    def test_crew_sections_also_include_member_entries(self) -> None:
        # show_wash_crew implies show_watch_party_member -- WASH Crew
        # inherits every Watch Party member capability.
        sections = command_sections(show_wash_crew=True)
        commands = [entry.name for _, entries in sections for entry in entries]
        self.assertIn("/add", commands)
        self.assertIn("/list", commands)
        self.assertIn("/vote_status", commands)

    def test_sections_preserve_declared_order(self) -> None:
        sections = command_sections(show_wash_crew=True)
        self.assertEqual(
            [name for name, _ in sections],
            [
                "General",
                "WASH Crew: Membership",
                "WASH Crew: Configuration",
                "Watch Items",
                "WASH Crew: Voting",
                "Voting",
                "WASH Crew: Suggestion Databases",
                "WASH Crew: Maintenance",
                "WASH Crew: Diagnostics",
                "Watch Parties",
                "WASH Crew: Watch Parties",
            ],
        )

    def test_command_text_is_generated_from_registry(self) -> None:
        text = build_command_help_text(show_wash_crew=True)
        for entry in COMMAND_HELP:
            self.assertIn(entry.name, text)
            self.assertIn(entry.summary, text)

    def test_everyone_command_text_hides_crew_and_member_content(self) -> None:
        text = build_command_help_text(show_wash_crew=False)
        self.assertNotIn("WASH Crew", text)
        self.assertNotIn("/diagnostics", text)
        self.assertNotIn("/add", text)

    def test_watch_party_member_command_text_hides_crew_content(self) -> None:
        text = build_command_help_text(show_wash_crew=False, show_watch_party_member=True)
        self.assertNotIn("WASH Crew", text)
        self.assertNotIn("/diagnostics", text)
        self.assertIn("/add", text)

    def test_all_crew_commands_use_crew_audience(self) -> None:
        crew_commands = [entry for entry in COMMAND_HELP if entry.name.startswith("/database_")]
        self.assertTrue(crew_commands)
        self.assertTrue(
            all(entry.audience is HelpAudience.WASH_CREW for entry in crew_commands)
        )

    # --- FR-024: /reject and /unreject no longer appear in /help -----------------

    def test_registry_no_longer_lists_reject(self) -> None:
        names = [entry.name for entry in COMMAND_HELP]
        self.assertNotIn("/reject", names)

    def test_registry_no_longer_lists_unreject(self) -> None:
        names = [entry.name for entry in COMMAND_HELP]
        self.assertNotIn("/unreject", names)

    def test_member_command_text_does_not_advertise_reject(self) -> None:
        text = build_command_help_text(show_wash_crew=False)
        self.assertNotIn("/reject", text)
        self.assertNotIn("/unreject", text)

    def test_crew_command_text_does_not_advertise_reject(self) -> None:
        text = build_command_help_text(show_wash_crew=True)
        self.assertNotIn("/reject", text)
        self.assertNotIn("/unreject", text)

    # --- FR-029: /vote removed, /setup and /config added, audiences fixed --------

    def test_registry_no_longer_lists_vote(self) -> None:
        names = [entry.name for entry in COMMAND_HELP]
        self.assertNotIn("/vote", names)

    def test_command_text_never_advertises_vote(self) -> None:
        for show_wash_crew in (True, False):
            text = build_command_help_text(show_wash_crew=show_wash_crew, show_watch_party_member=True)
            self.assertNotIn("`/vote`", text)

    def test_setup_and_config_are_wash_crew_only(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        self.assertIs(entries["/setup"].audience, HelpAudience.WASH_CREW)
        self.assertIs(entries["/config"].audience, HelpAudience.WASH_CREW)

    def test_edit_vote_is_wash_crew_only(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        self.assertIs(entries["/edit_vote"].audience, HelpAudience.WASH_CREW)

    def test_start_vote_is_wash_crew_only(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        self.assertIs(entries["/start_vote"].audience, HelpAudience.WASH_CREW)

    def test_add_list_and_stats_are_the_only_watch_party_member_commands(self) -> None:
        member_commands = [entry.name for entry in COMMAND_HELP if entry.audience is HelpAudience.WATCH_PARTY_MEMBER]
        self.assertEqual(sorted(member_commands), ["/add", "/list", "/stats"])

    def test_remove_vote_status_watch_party_status_diagnostics_are_wash_crew_only(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        for name in ("/remove", "/vote_status", "/watch_party_status", "/diagnostics"):
            self.assertIs(entries[name].audience, HelpAudience.WASH_CREW)

    def test_edit_suggestion_is_wash_crew_only(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        self.assertIs(entries["/edit_suggestion"].audience, HelpAudience.WASH_CREW)

    # --- FR-031: /watch_party is WASH Crew only, existing tiers unchanged --------

    def test_watch_party_admin_command_is_wash_crew_only(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        self.assertIs(entries["/watch_party"].audience, HelpAudience.WASH_CREW)

    def test_watch_party_admin_command_hidden_from_everyone_and_watch_party_member(self) -> None:
        for show_watch_party_member in (False, True):
            sections = command_sections(show_wash_crew=False, show_watch_party_member=show_watch_party_member)
            commands = [entry.name for _, entries in sections for entry in entries]
            self.assertNotIn("/watch_party", commands)

    def test_watch_party_admin_command_visible_to_wash_crew(self) -> None:
        sections = command_sections(show_wash_crew=True)
        commands = [entry.name for _, entries in sections for entry in entries]
        self.assertIn("/watch_party", commands)

    def test_watch_party_admin_command_text_hidden_from_everyone(self) -> None:
        text = build_command_help_text(show_wash_crew=False)
        self.assertNotIn("/watch_party", text)

    # --- FR-032B: /database_backup and /database_restore are WASH Crew only -----

    def test_database_backup_and_restore_are_wash_crew_only(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        self.assertIs(entries["/database_backup"].audience, HelpAudience.WASH_CREW)
        self.assertIs(entries["/database_restore"].audience, HelpAudience.WASH_CREW)

    def test_database_backup_and_restore_hidden_from_everyone_and_watch_party_member(self) -> None:
        for show_watch_party_member in (False, True):
            sections = command_sections(show_wash_crew=False, show_watch_party_member=show_watch_party_member)
            commands = [entry.name for _, entries in sections for entry in entries]
            self.assertNotIn("/database_backup", commands)
            self.assertNotIn("/database_restore", commands)

    # --- FR-032C: /database_reset, /factory_reset, /import are WASH Crew only ---

    def test_reset_and_import_commands_are_wash_crew_only(self) -> None:
        entries = {entry.name: entry for entry in COMMAND_HELP}
        for name in ("/database_reset", "/factory_reset", "/import"):
            self.assertIs(entries[name].audience, HelpAudience.WASH_CREW)

    def test_reset_and_import_commands_hidden_from_everyone_and_watch_party_member(self) -> None:
        for show_watch_party_member in (False, True):
            sections = command_sections(show_wash_crew=False, show_watch_party_member=show_watch_party_member)
            commands = [entry.name for _, entries in sections for entry in entries]
            for name in ("/database_reset", "/factory_reset", "/import"):
                self.assertNotIn(name, commands)

    def test_reset_and_import_commands_visible_to_wash_crew(self) -> None:
        sections = command_sections(show_wash_crew=True)
        commands = [entry.name for _, entries in sections for entry in entries]
        for name in ("/database_reset", "/factory_reset", "/import"):
            self.assertIn(name, commands)

    def test_existing_help_tier_visibility_is_unchanged(self) -> None:
        # The Everyone tier has never changed across any FR: only
        # /help, /about, /join_watch_party. The Watch Party Member tier
        # was /add-only through FR-032C; FR-033A deliberately extends it
        # with /list (Section 9: members may view lists privately), and
        # FR-034 extends it again with /stats (Section 4: members may
        # view their own/server statistics privately).
        everyone = [
            entry.name for _, entries in command_sections(show_wash_crew=False) for entry in entries
        ]
        self.assertEqual(sorted(everyone), sorted(["/help", "/about", "/join_watch_party"]))

        member = [
            entry.name
            for _, entries in command_sections(show_wash_crew=False, show_watch_party_member=True)
            for entry in entries
        ]
        self.assertEqual(
            sorted(member), sorted(["/help", "/about", "/join_watch_party", "/add", "/list", "/stats"])
        )


if __name__ == "__main__":
    unittest.main()
