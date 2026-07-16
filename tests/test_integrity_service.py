"""Tests for read-only persisted-data integrity checks."""

from __future__ import annotations

import unittest

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import MediaType, WatchItem
from watch_party_manager.services.integrity_service import (
    IntegrityReport,
    IntegrityService,
    IntegritySeverity,
)


class FakeSuggestionSource:
    def __init__(self, items=(), databases=()):
        self.items = list(items)
        self.databases = list(databases)

    def get_suggestions(self):
        return list(self.items)

    def list_databases(self, guild_id=None):
        databases = list(self.databases)
        if guild_id is not None:
            databases = [database for database in databases if database.guild_id == guild_id]
        return databases


def make_database(database_id=1, *, name="Movies", guild_id=10, channel_id=100):
    return SuggestionDatabase(
        database_id=database_id,
        name=name,
        guild_id=guild_id,
        channel_id=channel_id,
    )


def make_item(
    item_id=1,
    *,
    title="The Matrix",
    database_id=1,
    guild_id=10,
    channel_id=100,
    message_id=1000,
):
    return WatchItem(
        id=item_id,
        title=title,
        media_type=MediaType.MOVIE,
        database_id=database_id,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
    )


class IntegrityReportTests(unittest.TestCase):
    def test_empty_report_is_valid(self):
        report = IntegrityReport()
        self.assertTrue(report.is_valid)
        self.assertEqual((), report.errors)
        self.assertEqual((), report.warnings)

    def test_warnings_do_not_make_report_invalid(self):
        report = IntegrityService(FakeSuggestionSource([make_item(database_id=None)], [])).validate()
        self.assertTrue(report.is_valid)
        self.assertEqual(1, len(report.warnings))


class IntegrityServiceTests(unittest.TestCase):
    def validate(self, *, items=(), databases=(), guild_id=None):
        return IntegrityService(FakeSuggestionSource(items, databases)).validate(guild_id)

    def issue_codes(self, report):
        return [issue.code for issue in report.issues]

    def test_clean_data_has_no_issues(self):
        report = self.validate(items=[make_item()], databases=[make_database()])
        self.assertTrue(report.is_valid)
        self.assertEqual((), report.issues)

    def test_duplicate_database_ids_are_reported(self):
        report = self.validate(
            databases=[make_database(1), make_database(1, name="Other", channel_id=101)]
        )
        self.assertIn("duplicate_database_id", self.issue_codes(report))
        self.assertFalse(report.is_valid)

    def test_duplicate_database_names_are_case_insensitive_within_guild(self):
        report = self.validate(
            databases=[
                make_database(1, name="Movies", channel_id=100),
                make_database(2, name=" movies ", channel_id=101),
            ]
        )
        self.assertIn("duplicate_database_name", self.issue_codes(report))

    def test_same_database_name_is_allowed_in_different_guilds(self):
        report = self.validate(
            databases=[make_database(1), make_database(2, guild_id=20, channel_id=200)]
        )
        self.assertNotIn("duplicate_database_name", self.issue_codes(report))

    def test_duplicate_database_channel_is_scoped_to_guild(self):
        report = self.validate(
            databases=[make_database(1), make_database(2, name="Other")]
        )
        self.assertIn("duplicate_database_channel", self.issue_codes(report))

    def test_duplicate_watch_item_ids_are_reported(self):
        report = self.validate(
            items=[make_item(1), make_item(1, title="Alien", message_id=1001)],
            databases=[make_database()],
        )
        self.assertIn("duplicate_watch_item_id", self.issue_codes(report))

    def test_missing_watch_item_id_is_an_error(self):
        report = self.validate(items=[make_item(None)], databases=[make_database()])
        self.assertIn("missing_watch_item_id", self.issue_codes(report))
        self.assertFalse(report.is_valid)

    def test_duplicate_title_is_case_insensitive_within_database(self):
        report = self.validate(
            items=[
                make_item(1, title="Alien", message_id=1001),
                make_item(2, title=" alien ", message_id=1002),
            ],
            databases=[make_database()],
        )
        self.assertIn("duplicate_title_in_database", self.issue_codes(report))

    def test_same_title_is_allowed_in_different_databases(self):
        report = self.validate(
            items=[
                make_item(1, title="Alien", database_id=1, message_id=1001),
                make_item(2, title="Alien", database_id=2, channel_id=101, message_id=1002),
            ],
            databases=[make_database(1), make_database(2, name="Other", channel_id=101)],
        )
        self.assertNotIn("duplicate_title_in_database", self.issue_codes(report))

    def test_missing_database_reference_is_an_error(self):
        report = self.validate(items=[make_item(database_id=99)], databases=[make_database()])
        self.assertIn("missing_database_reference", self.issue_codes(report))

    def test_database_guild_mismatch_is_an_error(self):
        report = self.validate(items=[make_item(guild_id=20)], databases=[make_database()])
        self.assertIn("database_guild_mismatch", self.issue_codes(report))

    def test_orphaned_watch_item_is_a_warning(self):
        report = self.validate(items=[make_item(database_id=None)], databases=[make_database()])
        self.assertIn("orphaned_watch_item", self.issue_codes(report))
        self.assertEqual(IntegritySeverity.WARNING, report.warnings[0].severity)

    def test_partial_message_reference_is_a_warning(self):
        report = self.validate(
            items=[make_item(channel_id=None, message_id=None)],
            databases=[make_database()],
        )
        self.assertIn("partial_message_reference", self.issue_codes(report))

    def test_duplicate_complete_message_reference_is_an_error(self):
        report = self.validate(
            items=[make_item(1), make_item(2, title="Alien")],
            databases=[make_database()],
        )
        self.assertIn("duplicate_message_reference", self.issue_codes(report))

    def test_incomplete_references_are_not_compared_as_duplicates(self):
        report = self.validate(
            items=[
                make_item(1, channel_id=None, message_id=None),
                make_item(2, title="Alien", channel_id=None, message_id=None),
            ],
            databases=[make_database()],
        )
        self.assertNotIn("duplicate_message_reference", self.issue_codes(report))

    def test_guild_scope_ignores_other_guild_data(self):
        report = self.validate(
            guild_id=10,
            items=[
                make_item(1, guild_id=10),
                make_item(1, title="Other Guild", guild_id=20, database_id=2),
            ],
            databases=[make_database(1, guild_id=10), make_database(2, guild_id=20, channel_id=200)],
        )
        self.assertTrue(report.is_valid)

    def test_validate_does_not_mutate_source_lists(self):
        item = make_item()
        database = make_database()
        source = FakeSuggestionSource([item], [database])
        service = IntegrityService(source)

        service.validate()

        self.assertEqual([item], source.items)
        self.assertEqual([database], source.databases)


if __name__ == "__main__":
    unittest.main()
