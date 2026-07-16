"""Read-only consistency checks for persisted WASH data."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Sequence

from watch_party_manager.domain.suggestion_database import SuggestionDatabase
from watch_party_manager.domain.watch_item import WatchItem


class IntegritySeverity(str, Enum):
    """Severity assigned to an integrity issue."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    """One consistency problem found in persisted data."""

    code: str
    severity: IntegritySeverity
    message: str


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """Immutable result of an integrity scan."""

    issues: tuple[IntegrityIssue, ...] = ()

    @property
    def errors(self) -> tuple[IntegrityIssue, ...]:
        """Return issues that can make persisted relationships ambiguous or invalid."""
        return tuple(issue for issue in self.issues if issue.severity is IntegritySeverity.ERROR)

    @property
    def warnings(self) -> tuple[IntegrityIssue, ...]:
        """Return non-fatal issues that should still be reviewed."""
        return tuple(issue for issue in self.issues if issue.severity is IntegritySeverity.WARNING)

    @property
    def is_valid(self) -> bool:
        """Return True when the scan found no errors."""
        return not self.errors


class SuggestionIntegritySource(Protocol):
    """Minimum suggestion API required by IntegrityService."""

    def get_suggestions(self) -> Sequence[WatchItem]: ...

    def list_databases(self, guild_id: int | None = None) -> Sequence[SuggestionDatabase]: ...


class IntegrityService:
    """Validate relationships and uniqueness rules in suggestion data.

    The service is deliberately read-only. It reports problems but never
    mutates or repairs data, making it safe to use from diagnostics and tests.
    """

    def __init__(self, suggestion_source: SuggestionIntegritySource) -> None:
        self._suggestion_source = suggestion_source

    def validate(self, guild_id: int | None = None) -> IntegrityReport:
        """Scan suggestion databases and watch items for consistency.

        Args:
            guild_id: Optional Discord guild scope. When supplied, only that
                guild's databases and watch items are checked.
        """
        databases = list(self._suggestion_source.list_databases(guild_id))
        watch_items = list(self._suggestion_source.get_suggestions())
        if guild_id is not None:
            watch_items = [item for item in watch_items if item.guild_id == guild_id]

        issues: list[IntegrityIssue] = []
        issues.extend(self._check_databases(databases))
        issues.extend(self._check_watch_items(watch_items, databases))
        return IntegrityReport(tuple(issues))

    @staticmethod
    def _check_databases(databases: Sequence[SuggestionDatabase]) -> list[IntegrityIssue]:
        issues: list[IntegrityIssue] = []
        issues.extend(
            IntegrityService._duplicate_value_issues(
                databases,
                key=lambda database: database.database_id,
                code="duplicate_database_id",
                description="database ID",
            )
        )
        issues.extend(
            IntegrityService._duplicate_value_issues(
                databases,
                key=lambda database: (database.guild_id, database.name.casefold()),
                code="duplicate_database_name",
                description="database name within a guild",
            )
        )
        issues.extend(
            IntegrityService._duplicate_value_issues(
                databases,
                key=lambda database: (database.guild_id, database.channel_id),
                code="duplicate_database_channel",
                description="database channel within a guild",
            )
        )
        return issues

    @staticmethod
    def _check_watch_items(
        watch_items: Sequence[WatchItem],
        databases: Sequence[SuggestionDatabase],
    ) -> list[IntegrityIssue]:
        issues: list[IntegrityIssue] = []
        databases_by_id = {database.database_id: database for database in databases}

        items_with_ids = [item for item in watch_items if item.id is not None]
        issues.extend(
            IntegrityService._duplicate_value_issues(
                items_with_ids,
                key=lambda item: item.id,
                code="duplicate_watch_item_id",
                description="watch item ID",
            )
        )

        missing_ids = [item.title for item in watch_items if item.id is None]
        if missing_ids:
            issues.append(
                IntegrityIssue(
                    code="missing_watch_item_id",
                    severity=IntegritySeverity.ERROR,
                    message=f"{len(missing_ids)} watch item(s) have no persisted ID.",
                )
            )

        issues.extend(
            IntegrityService._duplicate_value_issues(
                watch_items,
                key=lambda item: (item.database_id, item.title.casefold()),
                code="duplicate_title_in_database",
                description="watch item title within a database",
            )
        )

        message_references = [
            item
            for item in watch_items
            if item.guild_id is not None
            and item.channel_id is not None
            and item.message_id is not None
        ]
        issues.extend(
            IntegrityService._duplicate_value_issues(
                message_references,
                key=lambda item: (item.guild_id, item.channel_id, item.message_id),
                code="duplicate_message_reference",
                description="Discord message reference",
            )
        )

        for item in watch_items:
            reference_parts = (item.guild_id, item.channel_id, item.message_id)
            present_reference_parts = sum(part is not None for part in reference_parts)
            if 0 < present_reference_parts < len(reference_parts):
                issues.append(
                    IntegrityIssue(
                        code="partial_message_reference",
                        severity=IntegritySeverity.WARNING,
                        message=f'Watch item "{item.title}" has an incomplete Discord message reference.',
                    )
                )

            if item.database_id is None:
                issues.append(
                    IntegrityIssue(
                        code="orphaned_watch_item",
                        severity=IntegritySeverity.WARNING,
                        message=f'Watch item "{item.title}" is not assigned to a suggestion database.',
                    )
                )
                continue

            database = databases_by_id.get(item.database_id)
            if database is None:
                issues.append(
                    IntegrityIssue(
                        code="missing_database_reference",
                        severity=IntegritySeverity.ERROR,
                        message=(
                            f'Watch item "{item.title}" references missing database '
                            f"{item.database_id}."
                        ),
                    )
                )
                continue

            if item.guild_id is not None and item.guild_id != database.guild_id:
                issues.append(
                    IntegrityIssue(
                        code="database_guild_mismatch",
                        severity=IntegritySeverity.ERROR,
                        message=(
                            f'Watch item "{item.title}" belongs to guild {item.guild_id} '
                            f"but database {database.database_id} belongs to guild {database.guild_id}."
                        ),
                    )
                )

        return issues

    @staticmethod
    def _duplicate_value_issues(
        values: Sequence[object],
        *,
        key,
        code: str,
        description: str,
    ) -> list[IntegrityIssue]:
        counts: dict[object, int] = defaultdict(int)
        for value in values:
            counts[key(value)] += 1

        return [
            IntegrityIssue(
                code=code,
                severity=IntegritySeverity.ERROR,
                message=f"Duplicate {description}: {value!r} appears {count} times.",
            )
            for value, count in counts.items()
            if count > 1
        ]
