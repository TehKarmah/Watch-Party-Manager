import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.help_registry import (
    COMMAND_HELP,
    GLOSSARY,
    CommandHelp,
    GlossaryEntry,
    HelpAudience,
    build_command_help_text,
    build_glossary_text,
    command_sections,
    find_glossary_entry,
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
                "WASH Crew: Suggestion Databases",
                "WASH Crew: Maintenance",
                "WASH Crew: Diagnostics",
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

    def test_glossary_has_unique_terms(self) -> None:
        terms = [entry.term.casefold() for entry in GLOSSARY]
        self.assertEqual(len(terms), len(set(terms)))

    def test_glossary_entry_validates_required_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "term is required"):
            GlossaryEntry(" ", "Definition")
        with self.assertRaisesRegex(ValueError, "definition is required"):
            GlossaryEntry("Term", " ")

    def test_glossary_lookup_matches_term_case_insensitively(self) -> None:
        entry = find_glossary_entry("  watch ITEM ")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.term, "Watch Item")

    def test_glossary_lookup_matches_alias(self) -> None:
        entry = find_glossary_entry("admin")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.term, "WASH Crew")

    def test_glossary_lookup_returns_none_for_unknown_or_blank_term(self) -> None:
        self.assertIsNone(find_glossary_entry("not a real term"))
        self.assertIsNone(find_glossary_entry("  "))

    def test_glossary_text_contains_all_terms_and_definitions(self) -> None:
        text = build_glossary_text()
        for entry in GLOSSARY:
            self.assertIn(entry.term, text)
            self.assertIn(entry.definition, text)

    def test_expected_core_definitions_are_present(self) -> None:
        terms = {entry.term for entry in GLOSSARY}
        self.assertTrue(
            {
                "Watch Item",
                "Suggestion Database",
                "WASH Crew",
                "Blind Vote",
                "Visible Vote",
                "Journey",
                "Rotation",
            }.issubset(terms)
        )

    def test_all_crew_commands_use_crew_audience(self) -> None:
        crew_commands = [entry for entry in COMMAND_HELP if entry.name.startswith("/database_")]
        self.assertTrue(crew_commands)
        self.assertTrue(
            all(entry.audience is HelpAudience.WASH_CREW for entry in crew_commands)
        )


if __name__ == "__main__":
    unittest.main()
