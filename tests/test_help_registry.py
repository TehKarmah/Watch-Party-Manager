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

    def test_member_sections_exclude_wash_crew_entries(self) -> None:
        sections = command_sections(show_wash_crew=False)
        commands = [entry.name for _, entries in sections for entry in entries]
        self.assertIn("/help", commands)
        self.assertIn("/about", commands)
        self.assertNotIn("/ping", commands)
        self.assertNotIn("/version", commands)
        self.assertNotIn("/database_add", commands)
        self.assertNotIn("/diagnostics", commands)

    def test_crew_sections_include_administrative_entries(self) -> None:
        sections = command_sections(show_wash_crew=True)
        commands = [entry.name for _, entries in sections for entry in entries]
        self.assertIn("/database_add", commands)
        self.assertIn("/diagnostics", commands)
        self.assertIn("/repair_suggestions", commands)

    def test_sections_preserve_declared_order(self) -> None:
        sections = command_sections(show_wash_crew=True)
        self.assertEqual(
            [name for name, _ in sections],
            [
                "General",
                "Watch Items",
                "Voting",
                "WASH Crew: Voting",
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

    def test_member_command_text_hides_crew_content(self) -> None:
        text = build_command_help_text(show_wash_crew=False)
        self.assertNotIn("WASH Crew", text)
        self.assertNotIn("/diagnostics", text)

    def test_all_crew_commands_use_crew_audience(self) -> None:
        crew_commands = [entry for entry in COMMAND_HELP if entry.name.startswith("/database_")]
        self.assertTrue(crew_commands)
        self.assertTrue(
            all(entry.audience is HelpAudience.WASH_CREW for entry in crew_commands)
        )


if __name__ == "__main__":
    unittest.main()
