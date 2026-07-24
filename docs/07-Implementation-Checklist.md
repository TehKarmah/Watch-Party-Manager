# Watch Party Manager Implementation Checklist

This checklist tracks implemented foundations against the Version 1 specification. `project_state.md` is authoritative for the current milestone and test baseline.

| ID | Requirement | Status | Notes |
| --- | --- | --- | --- |
| FR-001 | Define the Watch Item domain model | Complete | Validation, normalization, metadata, and status behavior are tested. |
| FR-002 | Implement the Watch Item Journey domain model | Complete foundation | Rotation history, rejection/retirement, and vote-win recording are wired into voting completion. Automatic "watched" marking is not yet produced by any code path. |
| FR-003 | Implement suggestion databases | Complete | Guild-scoped JSON persistence, per-database configuration, and administration (including per-database backup/restore/reset) are implemented. |
| FR-004 | Implement Watch Item suggestions | Complete | Add, list (filters, pagination, archive browsing), edit, remove (archive-preferring), IMDb normalization, duplicate detection, database association, post references, and persistence are implemented. |
| FR-005 | Implement nominee selection | Complete | Guild-scoped selection with configurable Rotation Pool / Soft Rotation / Infinite Pool strategies, candidate-count validation, and rotation lifecycle tracking. |
| FR-006 | Implement voting rounds and ballots | Complete foundation | Blind/visible modes, duration, vote changes, persistence, and ballot validation are implemented. |
| FR-007 | Implement standings and winner calculation | Complete | Deterministic ordering, totals, winners, and ties are implemented. |
| FR-008 | Implement Discord voting interaction | Complete | Interactive controls and persistent restoration are implemented. |
| FR-009 | Complete expired voting rounds | Complete | Automatic closing, winner announcements, restart safety, and journey updates are implemented. |
| FR-010 | Implement watch history | Partial | Winner recording into Watch Item Journey (times won, rotation history) is implemented. Marking an item "watched" and retroactive correction are not yet implemented. |
| FR-011 | Implement scheduling and Discord Events | Partial | Single-occurrence scheduled watch parties (schedule/reschedule/cancel/reminders) are implemented. The richer recurring Event Series model and native Discord Scheduled Event publishing are not. |
| FR-012 | Implement reminders and recurring event behavior | Partial | Vote-ending and watch-party reminders are implemented. Recurring event behavior depends on the Event Series foundation (FR-011). |
| FR-013 | Expand statistics and reporting | Complete foundation | Server, member, suggestion, rotation, and database statistics are implemented via `/stats`, derived from historical data with no running counters. |
| FR-014 | Implement setup and administration workflows | Complete | Guided, resumable `/setup` wizard (Back navigation, Save & Finish Later, resume-with-progress detection) and an always-available `/config` menu cover WASH Crew/Watch Party roles, suggestion databases, voting/reminder/backup defaults, and candidate-selection mode -- setup and `/config` read and write the same persisted values. |
| FR-015 | Implement backup, restore, import, and export | Complete foundation | Manual backup, validated restore (select or upload, with a pre-restore summary and confirmation), safety backups, single-suggestion-database backup/restore (merge or replace), suggestion-database reset, factory reset, and cross-instance import (merge or replace, typed-confirmation-gated) are implemented. Configurable scheduled backup *execution* is not. |
| FR-016 | Implement migration support | Not started | Required before persistent format changes become necessary. |

FR-017 and later (membership workflows, IMDb link normalization/duplicate detection, candidate selection & rotation, statistics, and this documentation pass) are implemented but not yet individually broken out into this table -- see `project_state.md`'s Functional Requirement Status for the current, area-based summary.

## Cross-Cutting Foundations

| Foundation | Status |
| --- | --- |
| WASH Crew fail-closed authorization | Complete |
| Guild scoping for implemented database operations | Complete |
| Discord timestamp formatting | Complete foundation |
| Diagnostics info (via `/about`, WASH Crew only) | Complete foundation |
| Startup data-integrity checks | Complete foundation |
| Structured application logging | Complete foundation |
| Automated test suite | 2426 passing tests |
