"""Services for Watch Party Manager."""

from watch_party_manager.services.suggestion_service import (
    SuggestionResult,
    SuggestionService,
)
from watch_party_manager.services.vote_service import (
    SuggestionLookup,
    VoteResult,
    VoteRoundResult,
    VoteService,
)
from watch_party_manager.services.nominee_selection_service import NomineeSelectionService
from watch_party_manager.services.integrity_service import (
    IntegrityIssue,
    IntegrityReport,
    IntegrityService,
    IntegritySeverity,
)
from watch_party_manager.services.suggestion_list_formatter import (
    SuggestionListFormatter,
    SuggestionListView,
)
from watch_party_manager.services.suggestion_repair_service import (
    SuggestionRepairReport,
    SuggestionRepairService,
)
from watch_party_manager.services.backup_service import (
    BackupCreationResult,
    BackupError,
    BackupFile,
    BackupKind,
    BackupManifest,
    BackupRestoreResult,
    BackupScheduleSettings,
    BackupService,
    BackupValidationResult,
)
from watch_party_manager.services.permission_service import PermissionCheck, PermissionService
from watch_party_manager.services.statistics_service import (
    StatisticsService,
    StatisticsSnapshot,
)
from watch_party_manager.services.vote_completion_service import (
    VoteCompletionResult,
    VoteCompletionService,
)
from watch_party_manager.services.watch_party_service import (
    WatchItemLookup,
    WatchPartyResult,
    WatchPartyService,
)

__all__ = [
    "SuggestionResult",
    "SuggestionService",
    "SuggestionLookup",
    "VoteResult",
    "VoteRoundResult",
    "VoteService",
    "NomineeSelectionService",
    "IntegrityIssue",
    "IntegrityReport",
    "IntegrityService",
    "IntegritySeverity",
    "SuggestionListFormatter",
    "SuggestionListView",
    "SuggestionRepairReport",
    "SuggestionRepairService",
    "BackupCreationResult",
    "BackupError",
    "BackupFile",
    "BackupKind",
    "BackupManifest",
    "BackupRestoreResult",
    "BackupScheduleSettings",
    "BackupService",
    "BackupValidationResult",
    "PermissionCheck",
    "PermissionService",
    "StatisticsService",
    "StatisticsSnapshot",
    "VoteCompletionResult",
    "VoteCompletionService",
    "WatchItemLookup",
    "WatchPartyResult",
    "WatchPartyService",
]
