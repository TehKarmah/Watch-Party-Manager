import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.services.about_service import (
    ABOUT_FOOTER,
    COMMANDS_REFERENCE_URL,
    EXPANDED_HELP_URL,
    PROJECT_REPOSITORY_URL,
    TAGLINE,
    WASH_ACCENT_COLOR,
    AboutConfiguration,
    AboutHealth,
    AboutRuntime,
    build_about_content,
)


def _field(content, name):
    for field in content.fields:
        if field.name == name:
            return field
    return None


class AboutServiceEveryoneTests(unittest.TestCase):
    """The reduced view shown to everyone (show_expanded_sections=False)."""

    def test_title_and_tagline(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertEqual(content.title, "WASH")
        self.assertIn(TAGLINE, content.description)

    def test_includes_version_and_build(self) -> None:
        content = build_about_content("1.2.3", "2026.07.17")

        self.assertIn("`1.2.3`", content.description)
        self.assertIn("`2026.07.17`", content.description)

    def test_only_the_documentation_field_is_present(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertEqual(1, len(content.fields))
        self.assertEqual("Documentation", content.fields[0].name)

    def test_no_health_configuration_or_runtime_without_expansion(self) -> None:
        content = build_about_content(
            "1.0.0",
            "2026.07.17",
            health=AboutHealth(True, True, True, True),
            configuration=AboutConfiguration("Movie Night", 1, 5, 2, False),
            runtime_info=AboutRuntime("3.12.10", "2.6.0", "Test Guild"),
        )

        self.assertIsNone(_field(content, "Health"))
        self.assertIsNone(_field(content, "Configuration"))
        self.assertIsNone(_field(content, "Runtime"))

    def test_documentation_field_has_no_repository_thumbnail_bearing_url(self) -> None:
        # No embed-level `url` -- only the Documentation field's markdown
        # links should ever reference the repository (Branding Cleanup).
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertFalse(hasattr(content, "url"))

    def test_documentation_field_links_github_command_reference_and_expanded_help(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")

        documentation = _field(content, "Documentation")
        self.assertIn(f"[GitHub Repository]({PROJECT_REPOSITORY_URL})", documentation.value)
        self.assertIn(f"[Command Reference]({COMMANDS_REFERENCE_URL})", documentation.value)
        self.assertIn(f"[Expanded Help]({EXPANDED_HELP_URL})", documentation.value)

    def test_does_not_mention_repository_or_author_branding_outside_documentation(self) -> None:
        # "Watch Party Manager" and "TehKarmah" (Branding Cleanup) must not
        # appear anywhere except inside the Documentation field's links.
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertNotIn("Watch Party Manager", content.title)
        self.assertNotIn("Watch Party Manager", content.description)
        self.assertNotIn("TehKarmah", content.title)
        self.assertNotIn("TehKarmah", content.description)
        for field in content.fields:
            if field.name != "Documentation":
                self.assertNotIn("TehKarmah", field.value)

    def test_embed_presentation_metadata(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertEqual(content.color, WASH_ACCENT_COLOR)
        self.assertEqual(content.footer, ABOUT_FOOTER)

    def test_embed_metadata_is_discord_safe(self) -> None:
        content = build_about_content("1.0.0", "2026.07.17")

        self.assertGreaterEqual(content.color, 0)
        self.assertLessEqual(content.color, 0xFFFFFF)
        self.assertLessEqual(len(content.footer), 2048)
        for field in content.fields:
            self.assertLessEqual(len(field.name), 256)
            self.assertLessEqual(len(field.value), 1024)

    def test_rejects_blank_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "version is required"):
            build_about_content("  ", "2026.07.17")

    def test_rejects_blank_build(self) -> None:
        with self.assertRaisesRegex(ValueError, "build is required"):
            build_about_content("1.0.0", "  ")


class AboutServiceExpandedTests(unittest.TestCase):
    """The WASH Crew-only expanded view (show_expanded_sections=True) --
    covers the information moved over from the removed /diagnostics
    command."""

    def _content(self, **overrides):
        values = dict(
            version="1.0.0",
            build="2026.07.17",
            show_expanded_sections=True,
            health=AboutHealth(
                discord_connected=True,
                scheduler_running=True,
                interactive_voting_restored=True,
                omdb_configured=True,
            ),
            configuration=AboutConfiguration(
                active_database_name="Movie Night",
                database_count=3,
                watch_item_count=12,
                scheduled_watch_party_count=2,
                open_vote_round=True,
            ),
            runtime_info=AboutRuntime(
                python_version="3.12.10", discord_py_version="2.6.0", guild_name="Test Guild"
            ),
        )
        values.update(overrides)
        return build_about_content(values.pop("version"), values.pop("build"), **values)

    def test_all_four_fields_present_when_expanded(self) -> None:
        content = self._content()

        names = [field.name for field in content.fields]
        self.assertEqual(["Health", "Configuration", "Runtime", "Documentation"], names)

    # --- Health ------------------------------------------------------------

    def test_health_reports_connected_scheduler_and_omdb(self) -> None:
        content = self._content(latency_ms=42.6)

        health = _field(content, "Health").value
        self.assertIn("Discord connection: 🟢", health)
        self.assertIn("43 ms", health)
        self.assertIn("Scheduler: 🟢 Running", health)
        self.assertIn("Interactive voting restored: Yes", health)
        self.assertIn("OMDb integration: 🟢 Configured", health)

    def test_health_reports_disconnected_stopped_and_unconfigured(self) -> None:
        content = self._content(
            health=AboutHealth(
                discord_connected=False,
                scheduler_running=False,
                interactive_voting_restored=False,
                omdb_configured=False,
            )
        )

        health = _field(content, "Health").value
        self.assertIn("Discord connection: 🔴 Disconnected", health)
        self.assertIn("Scheduler: 🔴 Stopped", health)
        self.assertIn("Interactive voting restored: No", health)
        self.assertIn("OMDb integration: 🔴 Not configured", health)

    def test_health_latency_indicator_changes_by_threshold(self) -> None:
        good = self._content(latency_ms=249)
        slow = self._content(latency_ms=250)
        poor = self._content(latency_ms=500)

        self.assertIn("🟢 Good (249 ms)", _field(good, "Health").value)
        self.assertIn("🟡 Slow (250 ms)", _field(slow, "Health").value)
        self.assertIn("🔴 Poor (500 ms)", _field(poor, "Health").value)

    # --- Configuration -------------------------------------------------------

    def test_configuration_shows_active_database_and_counts(self) -> None:
        content = self._content()

        configuration = _field(content, "Configuration").value
        self.assertIn("Active suggestion database: Movie Night", configuration)
        self.assertIn("Suggestion databases: 3", configuration)
        self.assertIn("Watch items: 12", configuration)
        self.assertIn("Scheduled watch parties: 2", configuration)
        self.assertIn("Active voting round: Yes", configuration)

    def test_configuration_reports_no_active_voting_round(self) -> None:
        content = self._content(
            configuration=AboutConfiguration("Movie Night", 3, 12, 2, open_vote_round=False)
        )

        self.assertIn("Active voting round: No", _field(content, "Configuration").value)

    # --- Runtime -------------------------------------------------------------

    def test_runtime_shows_python_discord_py_uptime_and_guild(self) -> None:
        started = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
        content = self._content(
            started_at=started, now=started + timedelta(hours=2, minutes=3, seconds=4)
        )

        runtime = _field(content, "Runtime").value
        self.assertIn("Python: 3.12.10", runtime)
        self.assertIn("discord.py: 2.6.0", runtime)
        self.assertIn("Uptime: 2h 3m 4s", runtime)
        self.assertIn("Server: Test Guild", runtime)

    def test_runtime_omits_uptime_without_timestamps(self) -> None:
        content = self._content(started_at=None, now=None)

        self.assertNotIn("Uptime", _field(content, "Runtime").value)

    def test_runtime_does_not_expose_debugging_only_details(self) -> None:
        content = self._content()

        runtime = _field(content, "Runtime").value
        self.assertNotIn(".json", runtime)
        self.assertNotIn("data/", runtime)
        self.assertNotIn("Traceback", runtime)


if __name__ == "__main__":
    unittest.main()
