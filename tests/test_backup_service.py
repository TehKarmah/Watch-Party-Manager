"""Tests for WASH JSON backup creation, validation, retention, and restore."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from watch_party_manager.services.backup_service import (
    BACKUP_FORMAT_VERSION,
    MANIFEST_NAME,
    DEFAULT_BACKUP_INTERVAL_DAYS,
    DEFAULT_RETENTION_LIMIT,
    BackupError,
    BackupKind,
    BackupScheduleSettings,
    BackupService,
)


class BackupServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.data_directory = self.root / "data"
        self.backup_directory = self.data_directory / "backups"
        self.settings = BackupScheduleSettings(
            interval_days=3,
            scheduled_retention_limit=2,
            manual_retention_limit=2,
        )
        self.service = BackupService(
            self.data_directory,
            self.backup_directory,
            settings=self.settings,
        )
        self.created_at = datetime(2026, 7, 17, 12, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write_json(self, relative_path, value):
        path = self.data_directory / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_schedule_defaults_to_daily_and_thirty_backups(self):
        settings = BackupScheduleSettings()

        self.assertTrue(settings.enabled)
        self.assertEqual(DEFAULT_BACKUP_INTERVAL_DAYS, settings.interval_days)
        self.assertEqual(1, settings.interval_days)
        self.assertEqual(DEFAULT_RETENTION_LIMIT, settings.scheduled_retention_limit)
        self.assertEqual(30, settings.scheduled_retention_limit)
        self.assertEqual(DEFAULT_RETENTION_LIMIT, settings.manual_retention_limit)

    def test_schedule_values_must_be_positive(self):
        for kwargs in (
            {"interval_days": 0},
            {"scheduled_retention_limit": 0},
            {"manual_retention_limit": 0},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, "at least 1"):
                    BackupScheduleSettings(**kwargs)

    def test_create_backup_includes_all_json_data_files_and_manifest(self):
        self.write_json("suggestions.json", {"suggestions": []})
        self.write_json("nested/votes.json", {"rounds": []})
        (self.data_directory / "notes.txt").write_text("ignore", encoding="utf-8")

        result = self.service.create_backup(created_at=self.created_at)

        self.assertTrue(result.archive_path.is_file())
        self.assertEqual(BackupKind.MANUAL, result.manifest.kind)
        self.assertEqual(
            ("nested/votes.json", "suggestions.json"),
            tuple(entry.path for entry in result.manifest.files),
        )
        with zipfile.ZipFile(result.archive_path) as archive:
            self.assertEqual(
                {MANIFEST_NAME, "nested/votes.json", "suggestions.json"},
                set(archive.namelist()),
            )

    def test_create_backup_ignores_json_files_inside_backup_directory(self):
        self.write_json("suggestions.json", {"suggestions": []})
        self.write_json("backups/should-not-be-copied.json", {"bad": True})

        result = self.service.create_backup(created_at=self.created_at)

        self.assertEqual(("suggestions.json",), tuple(item.path for item in result.manifest.files))

    def test_create_empty_backup_is_valid(self):
        result = self.service.create_backup(created_at=self.created_at)

        validation = self.service.validate_backup(result.archive_path)

        self.assertTrue(validation.is_valid)
        self.assertEqual((), validation.manifest.files)

    def test_naive_creation_datetime_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.service.create_backup(created_at=datetime(2026, 7, 17, 12, 30))

    def test_scheduled_backup_is_due_when_none_exist(self):
        self.assertTrue(self.service.is_scheduled_backup_due(now=self.created_at))

    def test_scheduled_backup_is_not_due_before_interval(self):
        self.write_json("suggestions.json", {"suggestions": []})
        self.service.create_backup(
            BackupKind.SCHEDULED,
            created_at=self.created_at,
        )

        self.assertFalse(
            self.service.is_scheduled_backup_due(
                now=datetime(2026, 7, 19, 12, 29, tzinfo=timezone.utc)
            )
        )

    def test_scheduled_backup_is_due_at_configured_interval(self):
        self.write_json("suggestions.json", {"suggestions": []})
        self.service.create_backup(
            BackupKind.SCHEDULED,
            created_at=self.created_at,
        )

        self.assertTrue(
            self.service.is_scheduled_backup_due(
                now=datetime(2026, 7, 20, 12, 30, tzinfo=timezone.utc)
            )
        )

    def test_disabled_scheduled_backups_are_never_due(self):
        service = BackupService(
            self.data_directory,
            self.backup_directory,
            settings=BackupScheduleSettings(enabled=False),
        )

        self.assertFalse(service.is_scheduled_backup_due(now=self.created_at))

    def test_retention_limits_can_differ_by_backup_kind(self):
        settings = BackupScheduleSettings(
            scheduled_retention_limit=4,
            manual_retention_limit=9,
        )
        service = BackupService(
            self.data_directory,
            self.backup_directory,
            settings=settings,
        )

        self.assertEqual(4, service.retention_limit_for(BackupKind.SCHEDULED))
        self.assertEqual(9, service.retention_limit_for(BackupKind.MANUAL))

    def test_validate_backup_accepts_a_created_archive(self):
        self.write_json("suggestions.json", {"suggestions": [{"title": "Alien"}]})
        result = self.service.create_backup(created_at=self.created_at)

        validation = self.service.validate_backup(result.archive_path)

        self.assertTrue(validation.is_valid)
        self.assertEqual(BACKUP_FORMAT_VERSION, validation.manifest.format_version)

    def test_validate_backup_rejects_missing_archive(self):
        validation = self.service.validate_backup(self.root / "missing.zip")

        self.assertFalse(validation.is_valid)
        self.assertIn("does not exist", validation.errors[0])

    def test_validate_backup_rejects_missing_manifest(self):
        archive_path = self.root / "missing-manifest.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("suggestions.json", "{}")

        validation = self.service.validate_backup(archive_path)

        self.assertFalse(validation.is_valid)
        self.assertIn("manifest is missing", validation.errors[0])

    def test_validate_backup_rejects_tampered_payload(self):
        self.write_json("suggestions.json", {"suggestions": []})
        result = self.service.create_backup(created_at=self.created_at)
        rewritten = self.root / "tampered.zip"
        with zipfile.ZipFile(result.archive_path, "r") as source, zipfile.ZipFile(
            rewritten, "w"
        ) as destination:
            for name in source.namelist():
                payload = source.read(name)
                if name == "suggestions.json":
                    payload = b'{"suggestions": ["tampered"]}'
                destination.writestr(name, payload)

        validation = self.service.validate_backup(rewritten)

        self.assertFalse(validation.is_valid)
        self.assertTrue(
            any("mismatch" in error.casefold() for error in validation.errors),
            validation.errors,
        )

    def test_validate_backup_rejects_unsafe_manifest_path(self):
        archive_path = self.root / "unsafe.zip"
        payload = b"{}"
        manifest = {
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": "2026-07-17T12:30:00Z",
            "kind": "manual",
            "files": [
                {
                    "path": "../outside.json",
                    "size": len(payload),
                    "sha256": __import__("hashlib").sha256(payload).hexdigest(),
                }
            ],
        }
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest))
            archive.writestr("../outside.json", payload)

        validation = self.service.validate_backup(archive_path)

        self.assertFalse(validation.is_valid)
        self.assertTrue(any("Unsafe backup path" in error for error in validation.errors))

    def test_retention_removes_only_older_backups_of_same_kind(self):
        self.write_json("suggestions.json", {"suggestions": []})
        first = self.service.create_backup(
            created_at=datetime(2026, 7, 17, 10, tzinfo=timezone.utc)
        ).archive_path
        second = self.service.create_backup(
            created_at=datetime(2026, 7, 17, 11, tzinfo=timezone.utc)
        ).archive_path
        third_result = self.service.create_backup(
            created_at=datetime(2026, 7, 17, 12, tzinfo=timezone.utc)
        )
        daily = self.service.create_backup(
            BackupKind.SCHEDULED,
            created_at=datetime(2026, 7, 17, 13, tzinfo=timezone.utc),
        ).archive_path

        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue(third_result.archive_path.exists())
        self.assertTrue(daily.exists())
        self.assertEqual((first,), third_result.removed_archives)

    def test_list_backups_returns_newest_first(self):
        self.write_json("suggestions.json", {"suggestions": []})
        older = self.service.create_backup(
            created_at=datetime(2026, 7, 17, 10, tzinfo=timezone.utc)
        ).archive_path
        newer = self.service.create_backup(
            created_at=datetime(2026, 7, 17, 11, tzinfo=timezone.utc)
        ).archive_path
        older.touch()
        newer.touch()
        older_time = 1_000_000_000
        newer_time = 2_000_000_000
        import os
        os.utime(older, ns=(older_time, older_time))
        os.utime(newer, ns=(newer_time, newer_time))

        self.assertEqual((newer, older), self.service.list_backups(BackupKind.MANUAL))

    def test_restore_replaces_declared_files_and_keeps_unrelated_json(self):
        suggestions = self.write_json("suggestions.json", {"value": "before"})
        result = self.service.create_backup(created_at=self.created_at)
        suggestions.write_text(json.dumps({"value": "after"}), encoding="utf-8")
        unrelated = self.write_json("newer.json", {"keep": True})

        restore = self.service.restore_backup(
            result.archive_path,
            create_safety_backup=False,
        )

        self.assertEqual({"value": "before"}, json.loads(suggestions.read_text()))
        self.assertEqual({"keep": True}, json.loads(unrelated.read_text()))
        self.assertEqual((suggestions,), restore.restored_files)
        self.assertIsNone(restore.safety_backup)

    def test_restore_creates_safety_backup_by_default(self):
        suggestions = self.write_json("suggestions.json", {"value": "before"})
        original = self.service.create_backup(created_at=self.created_at).archive_path
        suggestions.write_text(json.dumps({"value": "current"}), encoding="utf-8")

        restore = self.service.restore_backup(original)

        self.assertIsNotNone(restore.safety_backup)
        self.assertTrue(restore.safety_backup.is_file())
        self.assertTrue(self.service.validate_backup(restore.safety_backup).is_valid)

    def test_restore_rejects_invalid_archive_without_changing_data(self):
        suggestions = self.write_json("suggestions.json", {"value": "current"})
        bad_archive = self.root / "bad.zip"
        bad_archive.write_bytes(b"not a zip")

        with self.assertRaisesRegex(BackupError, "validation failed"):
            self.service.restore_backup(bad_archive)

        self.assertEqual({"value": "current"}, json.loads(suggestions.read_text()))


if __name__ == "__main__":
    unittest.main()
