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

    def test_about_content_includes_version_build_features_roles_and_project_in_description(self) -> None:
        content = build_about_content("1.2.3", "2026.07.17")

        self.assertIn("**Version & Build**", content.description)
        self.assertIn("`1.2.3`", content.description)
        self.assertIn("`2026.07.17`", content.description)
        self.assertIn("**Features**", content.description)
        self.assertIn("Statistics & diagnostics", content.description)
        self.assertIn("**Roles**", content.description)
        self.assertIn("Watch Party", content.description)
        self.assertIn("WASH Crew", content.description)
        self.assertIn("TehKarmah", content.description)
        self.assertIn(PROJECT_REPOSITORY_URL, content.description)

    def test_about_uses_no_embed_fields(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")
        self.assertEqual(content.fields, ())

    def test_about_can_include_latency_and_uptime(self) -> None:
        from datetime import datetime, timedelta, timezone

        started = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
        content = build_about_content(
            "1.0.0",
            "2026.07.17",
            latency_ms=42.6,
            started_at=started,
            now=started + timedelta(hours=2, minutes=3, seconds=4),
        )
        self.assertIn("**Status**", content.description)
        self.assertIn("Gateway latency: 43 ms", content.description)
        self.assertIn("Uptime: 2h 3m 4s", content.description)

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
