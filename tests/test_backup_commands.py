import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from watch_party_manager.bot import (
    find_backup_by_filename,
    perform_backup,
    perform_confirmed_restore_from_path,
    perform_restore_from_path,
    send_help_response,
)
from watch_party_manager.services.backup_service import (
    BackupError,
    BackupKind,
    BackupScheduleSettings,
    BackupService,
    BackupType,
)
from watch_party_manager.services.help_service import build_help_response

WASH_CREW_ROLE_ID = 999


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, roles=()) -> None:
        self.roles = list(roles)


class FakeResponse:
    def __init__(self) -> None:
        self.sent_messages = []
        self.sent_ephemeral = []

    async def send_message(self, content, ephemeral=False, view=None) -> None:
        self.sent_messages.append(content)
        self.sent_ephemeral.append(ephemeral)


class FakeFollowup:
    def __init__(self) -> None:
        self.sent_messages = []
        self.sent_ephemeral = []

    async def send(self, content, ephemeral=False) -> None:
        self.sent_messages.append(content)
        self.sent_ephemeral.append(ephemeral)


class FakeInteraction:
    def __init__(self) -> None:
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class SendHelpResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_response_sends_a_single_message_via_response(self) -> None:
        interaction = FakeInteraction()
        response = build_help_response(show_wash_crew=False)

        await send_help_response(interaction, response)

        self.assertEqual(interaction.response.sent_messages, [response.messages[0]])
        self.assertEqual(interaction.followup.sent_messages, [])

    async def test_wash_crew_receives_both_messages(self) -> None:
        interaction = FakeInteraction()
        response = build_help_response(show_wash_crew=True)

        await send_help_response(interaction, response)

        self.assertEqual(interaction.response.sent_messages, [response.messages[0]])
        self.assertEqual(interaction.followup.sent_messages, [response.messages[1]])

    async def test_wash_crew_messages_are_sent_in_order(self) -> None:
        interaction = FakeInteraction()
        response = build_help_response(show_wash_crew=True)

        await send_help_response(interaction, response)

        self.assertIn("**WASH Commands**", interaction.response.sent_messages[0])
        self.assertIn("**Expanded Help Documentation**", interaction.followup.sent_messages[0])
        self.assertIn("08-Expanded-Help.md", interaction.followup.sent_messages[0])

    async def test_all_messages_are_ephemeral(self) -> None:
        interaction = FakeInteraction()
        response = build_help_response(show_wash_crew=True)

        await send_help_response(interaction, response)

        self.assertEqual(interaction.response.sent_ephemeral, [True])
        self.assertEqual(interaction.followup.sent_ephemeral, [True])

    async def test_non_wash_crew_behavior_is_a_single_ephemeral_message(self) -> None:
        interaction = FakeInteraction()
        response = build_help_response(show_wash_crew=False)

        await send_help_response(interaction, response)

        self.assertEqual(len(interaction.response.sent_messages), 1)
        self.assertEqual(len(interaction.followup.sent_messages), 0)
        self.assertEqual(interaction.response.sent_ephemeral, [True])


class BackupCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        root = Path(self._temp_dir.name)
        self.data_directory = root / "data"
        self.backup_directory = self.data_directory / "backups"
        self.data_directory.mkdir(parents=True, exist_ok=True)
        (self.data_directory / "suggestions.json").write_text("{}", encoding="utf-8")
        self.backup_service = BackupService(
            self.data_directory,
            self.backup_directory,
            settings=BackupScheduleSettings(manual_retention_limit=10, scheduled_retention_limit=10),
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def _wash_crew_member(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(WASH_CREW_ROLE_ID)])

    def _regular_member(self) -> FakeMember:
        return FakeMember(roles=[FakeRole(1)])

    # --- /backup ------------------------------------------------------------

    def test_backup_succeeds_and_reports_creation(self) -> None:
        message, ephemeral, archive_path, display_filename = perform_backup(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID
        )

        self.assertTrue(ephemeral)
        self.assertIn("Backup created successfully", message)
        created = self.backup_service.list_backups(BackupKind.MANUAL)
        self.assertEqual(len(created), 1)
        self.assertEqual(archive_path, created[0])

    def test_backup_display_filename_uses_the_project_name_and_required_format(self) -> None:
        message, ephemeral, archive_path, display_filename = perform_backup(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID
        )

        self.assertRegex(
            display_filename, r"^Watch_Party_Manager_Backup_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.zip$"
        )
        self.assertIn(display_filename, message)
        # The internal archive keeps its existing wash-*.zip name -- only
        # the Discord-facing display name uses the project's own naming.
        self.assertTrue(archive_path.name.startswith("wash-manual-"))

    def test_backup_message_reports_creation_time_and_type(self) -> None:
        message, ephemeral, archive_path, display_filename = perform_backup(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID
        )

        self.assertIn("**Created:**", message)
        self.assertIn("**Type:** Manual", message)

    def test_backup_fails_closed_when_role_not_configured(self) -> None:
        message, ephemeral, archive_path, display_filename = perform_backup(
            self.backup_service, self._wash_crew_member(), None
        )

        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)
        self.assertIsNone(archive_path)
        self.assertIsNone(display_filename)
        self.assertEqual(self.backup_service.list_backups(), ())

    def test_backup_rejects_a_non_wash_crew_member(self) -> None:
        message, ephemeral, archive_path, display_filename = perform_backup(
            self.backup_service, self._regular_member(), WASH_CREW_ROLE_ID
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)
        self.assertIsNone(archive_path)
        self.assertIsNone(display_filename)
        self.assertEqual(self.backup_service.list_backups(), ())

    def test_backup_reports_failure_cleanly(self) -> None:
        with patch.object(
            self.backup_service, "create_backup", side_effect=BackupError("disk full")
        ):
            message, ephemeral, archive_path, display_filename = perform_backup(
                self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID
            )

        self.assertTrue(ephemeral)
        self.assertIn("Backup failed", message)
        self.assertIn("disk full", message)
        self.assertIsNone(archive_path)
        self.assertIsNone(display_filename)

    # --- /restore: validation before confirmation (perform_restore_from_path) --

    def test_restore_fails_closed_when_role_not_configured(self) -> None:
        message, ephemeral, needs_confirmation = perform_restore_from_path(
            self.backup_service, self._wash_crew_member(), None, self.data_directory.parent / "wash-manual-x.zip"
        )

        self.assertTrue(ephemeral)
        self.assertFalse(needs_confirmation)
        self.assertIn("not been configured", message)

    def test_restore_rejects_a_non_wash_crew_member(self) -> None:
        message, ephemeral, needs_confirmation = perform_restore_from_path(
            self.backup_service, self._regular_member(), WASH_CREW_ROLE_ID, self.data_directory.parent / "wash-manual-x.zip"
        )

        self.assertTrue(ephemeral)
        self.assertFalse(needs_confirmation)
        self.assertIn("WASH Crew", message)

    def test_restore_requests_confirmation_for_a_valid_backup(self) -> None:
        perform_backup(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID)
        real_backup = self.backup_service.list_backups()[0]

        message, ephemeral, needs_confirmation = perform_restore_from_path(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, real_backup
        )

        self.assertTrue(ephemeral)
        self.assertTrue(needs_confirmation)
        self.assertIn("Restore Summary", message)
        self.assertIn("automatically first", message)

    def test_restore_summary_reports_counts_when_determinable(self) -> None:
        (self.data_directory / "suggestions.json").write_text(
            '{"suggestions": [{"title": "Alien"}]}', encoding="utf-8"
        )
        perform_backup(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID)
        real_backup = self.backup_service.list_backups()[0]

        message, _, _ = perform_restore_from_path(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, real_backup
        )

        self.assertIn("Suggestions: 1", message)
        self.assertIn("Backup type: Full", message)

    def test_restore_does_not_touch_data_before_confirmation(self) -> None:
        perform_backup(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID)
        real_backup = self.backup_service.list_backups()[0]
        original_content = (self.data_directory / "suggestions.json").read_text(encoding="utf-8")

        perform_restore_from_path(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, real_backup)

        # No safety backup should have been created yet either, since
        # nothing was actually restored.
        self.assertEqual(len(self.backup_service.list_backups(BackupKind.MANUAL)), 1)
        self.assertEqual((self.data_directory / "suggestions.json").read_text(encoding="utf-8"), original_content)

    def test_restore_reports_a_backup_that_fails_validation(self) -> None:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        corrupt_path = self.backup_directory / BackupKind.MANUAL.value
        corrupt_path.mkdir(parents=True, exist_ok=True)
        corrupt_archive = corrupt_path / "wash-manual-corrupt.zip"
        with zipfile.ZipFile(corrupt_archive, "w") as archive:
            archive.writestr("not_the_manifest.txt", "oops")

        message, ephemeral, needs_confirmation = perform_restore_from_path(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, corrupt_archive
        )

        self.assertTrue(ephemeral)
        self.assertFalse(needs_confirmation)
        self.assertIn("failed validation", message)

    def test_restore_rejects_a_suggestion_database_backup(self) -> None:
        result = self.backup_service.create_scoped_backup(
            {"suggestion_databases.json": b'{"databases": []}'},
            kind=BackupKind.MANUAL,
            backup_type=BackupType.SUGGESTION_DATABASE,
        )

        message, ephemeral, needs_confirmation = perform_restore_from_path(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, result.archive_path
        )

        self.assertFalse(needs_confirmation)
        self.assertIn("Unsupported backup type", message)

    # --- /restore: confirmed restore (perform_confirmed_restore_from_path) -----

    def test_confirmed_restore_succeeds_and_reports_restored_file_count(self) -> None:
        perform_backup(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID)
        real_backup = self.backup_service.list_backups()[0]

        message, ephemeral = perform_confirmed_restore_from_path(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, real_backup
        )

        self.assertTrue(ephemeral)
        self.assertIn("Restored", message)
        self.assertIn("safety backup", message)

    def test_confirmed_restore_fails_closed_when_role_not_configured(self) -> None:
        perform_backup(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID)
        real_backup = self.backup_service.list_backups()[0]

        message, ephemeral = perform_confirmed_restore_from_path(
            self.backup_service, self._wash_crew_member(), None, real_backup
        )

        self.assertTrue(ephemeral)
        self.assertIn("not been configured", message)

    def test_confirmed_restore_rejects_a_non_wash_crew_member(self) -> None:
        perform_backup(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID)
        real_backup = self.backup_service.list_backups()[0]

        message, ephemeral = perform_confirmed_restore_from_path(
            self.backup_service, self._regular_member(), WASH_CREW_ROLE_ID, real_backup
        )

        self.assertTrue(ephemeral)
        self.assertIn("WASH Crew", message)

    def test_confirmed_restore_reports_a_missing_archive(self) -> None:
        perform_backup(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID)

        message, ephemeral = perform_confirmed_restore_from_path(
            self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID, self.data_directory.parent / "does-not-exist.zip"
        )

        self.assertTrue(ephemeral)
        self.assertIn("failed validation", message)

    # --- find_backup_by_filename ----------------------------------------------

    def test_find_backup_by_filename_returns_none_when_missing(self) -> None:
        self.assertIsNone(find_backup_by_filename(self.backup_service, "nope.zip"))

    def test_find_backup_by_filename_finds_an_existing_backup(self) -> None:
        perform_backup(self.backup_service, self._wash_crew_member(), WASH_CREW_ROLE_ID)
        real_backup = self.backup_service.list_backups()[0]

        found = find_backup_by_filename(self.backup_service, real_backup.name)

        self.assertEqual(found, real_backup)


if __name__ == "__main__":
    unittest.main()
