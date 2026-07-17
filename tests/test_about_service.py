import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.services.about_service import (
    ABOUT_FOOTER,
    PROJECT_REPOSITORY_URL,
    TAGLINE,
    WASH_ACCENT_COLOR,
    build_about_content,
)


class AboutServiceTests(unittest.TestCase):
    def test_about_content_uses_wash_identity_and_tagline(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertEqual(content.title, "WASH")
        self.assertIn("Watch Party Administration & Scheduling Helper", content.description)
        self.assertIn(TAGLINE, content.description)

    def test_about_content_includes_version_and_build(self) -> None:
        content = build_about_content("1.2.3", "2026.07.17")
        version_field = content.fields[0]

        self.assertEqual(version_field.name, "📦 Version & Build")
        self.assertIn("`1.2.3`", version_field.value)
        self.assertIn("`2026.07.17`", version_field.value)

    def test_about_content_includes_features_roles_and_project(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")
        fields = {field.name: field.value for field in content.fields}

        self.assertIn("🎬 Features", fields)
        self.assertIn("👥 Roles", fields)
        self.assertIn("📁 Project", fields)
        self.assertIn("Statistics & diagnostics", fields["🎬 Features"])
        self.assertIn("Watch Party", fields["👥 Roles"])
        self.assertIn("WASH Crew", fields["👥 Roles"])
        self.assertIn("TehKarmah", fields["📁 Project"])
        self.assertIn(PROJECT_REPOSITORY_URL, fields["📁 Project"])

    def test_about_fields_are_not_inline(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")
        self.assertTrue(all(not field.inline for field in content.fields))


    def test_about_content_includes_embed_presentation_metadata(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertEqual(content.url, PROJECT_REPOSITORY_URL)
        self.assertEqual(content.color, WASH_ACCENT_COLOR)
        self.assertEqual(content.footer, ABOUT_FOOTER)

    def test_about_embed_metadata_is_discord_safe(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertGreaterEqual(content.color, 0)
        self.assertLessEqual(content.color, 0xFFFFFF)
        self.assertLessEqual(len(content.footer), 2048)

    def test_about_content_rejects_blank_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "version is required"):
            build_about_content("  ", "2026.07.17")

    def test_about_content_rejects_blank_build(self) -> None:
        with self.assertRaisesRegex(ValueError, "build is required"):
            build_about_content("1.0.0", "  ")


if __name__ == "__main__":
    unittest.main()
