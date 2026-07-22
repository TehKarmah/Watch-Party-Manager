"""Tests for FR-032B's pre-restore summary builder (restore_summary_service.py)."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.services.backup_service import BackupKind, BackupScheduleSettings, BackupService, BackupType
from watch_party_manager.services.restore_summary_service import build_restore_summary
from watch_party_manager.version import __version__ as APPLICATION_VERSION

CREATED_AT = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


class RestoreSummaryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self._temp_dir.name)
        self.data_directory = self.root / "data"
        self.backup_directory = self.data_directory / "backups"
        self.service = BackupService(
            self.data_directory,
            self.backup_directory,
            settings=BackupScheduleSettings(),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def write_json(self, relative_path: str, value) -> Path:
        path = self.data_directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_invalid_archive_reports_errors_and_no_summary_fields(self) -> None:
        bad_archive = self.root / "bad.zip"
        bad_archive.write_bytes(b"not a zip")

        summary = build_restore_summary(self.service, bad_archive)

        self.assertFalse(summary.is_valid)
        self.assertTrue(summary.errors)
        self.assertIsNone(summary.backup_type)

    def test_valid_full_backup_reports_metadata(self) -> None:
        self.write_json("suggestions.json", {"suggestions": [{"title": "A"}, {"title": "B"}]})
        self.write_json("suggestion_databases.json", {"databases": [{"database_id": 1}]})
        self.write_json("voting.json", {"rounds": [{"round_id": 1}]})
        self.write_json("membership_requests.json", {"requests": [{"request_id": 1}]})
        self.write_json("guild_configurations.json", {"guilds": {"100": {"guild_id": 100}}})
        result = self.service.create_backup(created_at=CREATED_AT, guild_id=100)

        summary = build_restore_summary(self.service, result.archive_path)

        self.assertTrue(summary.is_valid)
        self.assertEqual(BackupType.FULL, summary.backup_type)
        self.assertEqual("Watch Party Manager", summary.project_name)
        self.assertEqual(APPLICATION_VERSION, summary.application_version)
        self.assertEqual(100, summary.guild_id)
        self.assertEqual(2, summary.suggestion_count)
        self.assertEqual(1, summary.suggestion_database_count)
        self.assertEqual(1, summary.vote_round_count)
        self.assertEqual(1, summary.membership_request_count)
        self.assertTrue(summary.configuration_present)
        self.assertEqual((), summary.warnings)

    def test_counts_are_none_when_the_file_is_absent_from_the_archive(self) -> None:
        result = self.service.create_backup(created_at=CREATED_AT)

        summary = build_restore_summary(self.service, result.archive_path)

        self.assertIsNone(summary.suggestion_count)
        self.assertIsNone(summary.vote_round_count)
        self.assertIsNone(summary.membership_request_count)
        self.assertIsNone(summary.suggestion_database_count)
        self.assertIsNone(summary.configuration_present)

    def test_configuration_present_is_false_when_guild_has_no_entry(self) -> None:
        self.write_json("guild_configurations.json", {"guilds": {"999": {"guild_id": 999}}})
        result = self.service.create_backup(created_at=CREATED_AT, guild_id=100)

        summary = build_restore_summary(self.service, result.archive_path)

        self.assertFalse(summary.configuration_present)

    def test_rejects_a_backup_whose_type_does_not_match_what_was_expected(self) -> None:
        result = self.service.create_scoped_backup(
            {"suggestion_databases.json": b'{"databases": []}'},
            kind=BackupKind.MANUAL,
            backup_type=BackupType.SUGGESTION_DATABASE,
            created_at=CREATED_AT,
        )

        summary = build_restore_summary(self.service, result.archive_path, expected_backup_type=BackupType.FULL)

        self.assertFalse(summary.is_valid)
        self.assertTrue(any("Unsupported backup type" in error for error in summary.errors))

    def test_accepts_a_backup_whose_type_matches_what_was_expected(self) -> None:
        result = self.service.create_scoped_backup(
            {"suggestion_databases.json": b'{"databases": []}'},
            kind=BackupKind.MANUAL,
            backup_type=BackupType.SUGGESTION_DATABASE,
            created_at=CREATED_AT,
            database_id=3,
            database_name="Movie Night",
        )

        summary = build_restore_summary(
            self.service, result.archive_path, expected_backup_type=BackupType.SUGGESTION_DATABASE
        )

        self.assertTrue(summary.is_valid)
        self.assertEqual(3, summary.database_id)
        self.assertEqual("Movie Night", summary.database_name)

    def test_warns_on_application_version_mismatch(self) -> None:
        self.write_json("suggestions.json", {"suggestions": []})
        result = self.service.create_backup(created_at=CREATED_AT)
        # Rewrite the manifest in-place with a different application_version.
        with zipfile.ZipFile(result.archive_path, mode="r") as archive:
            names = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(names["manifest.json"])
        manifest["application_version"] = "0.0.1-old"
        names["manifest.json"] = json.dumps(manifest).encode("utf-8")
        with zipfile.ZipFile(result.archive_path, mode="w") as archive:
            for name, payload in names.items():
                archive.writestr(name, payload)

        summary = build_restore_summary(self.service, result.archive_path)

        self.assertTrue(summary.is_valid)
        self.assertTrue(any("application version" in warning.lower() for warning in summary.warnings))

    def test_does_not_modify_live_data(self) -> None:
        suggestions = self.write_json("suggestions.json", {"suggestions": [{"title": "Original"}]})
        result = self.service.create_backup(created_at=CREATED_AT)
        original_content = suggestions.read_text(encoding="utf-8")

        build_restore_summary(self.service, result.archive_path)

        self.assertEqual(original_content, suggestions.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
