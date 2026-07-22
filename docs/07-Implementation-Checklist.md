# Watch Party Manager Implementation Checklist

This checklist tracks implemented foundations against the Version 1 specification. `project_state.md` is authoritative for the current milestone and test baseline.

| ID | Requirement | Status | Notes |
| --- | --- | --- | --- |
| FR-001 | Define the Watch Item domain model | Complete | Validation, normalization, metadata, and status behavior are tested. |
| FR-002 | Implement the Watch Item Journey domain model | Complete foundation | Model and validation are implemented; automatic vote-completion updates are pending. |
| FR-003 | Implement suggestion databases | Complete foundation | Guild-scoped JSON persistence and administration commands are implemented. |
| FR-004 | Implement Watch Item suggestions | Complete foundation | Add, list, remove, database association, post references, and persistence are implemented. |
| FR-005 | Implement nominee selection | Complete foundation | Guild-scoped intelligent selection and candidate-count validation are implemented. |
| FR-006 | Implement voting rounds and ballots | Complete foundation | Blind/visible modes, duration, vote changes, persistence, and ballot validation are implemented. |
| FR-007 | Implement standings and winner calculation | Complete | Deterministic ordering, totals, winners, and ties are implemented. |
| FR-008 | Implement Discord voting interaction | Complete | Interactive controls and persistent restoration are implemented. |
| FR-009 | Complete expired voting rounds | In progress | Automatic closing, announcements, restart safety, and journey updates are active work. |
| FR-010 | Implement watch history | Not started | Planned immediately after voting lifecycle completion. |
| FR-011 | Implement scheduling and Discord Events | Not started | Planned after watch history. |
| FR-012 | Implement reminders and recurring event behavior | Not started | Depends on scheduling foundations. |
| FR-013 | Expand statistics and reporting | Partial | Initial statistics service and `/stats` are implemented. |
| FR-014 | Implement setup and administration workflows | Partial | WASH Crew and database commands exist; guided setup is pending. |
| FR-015 | Implement backup, restore, import, and export | Complete foundation | Manual backup, validated restore (select or upload, with a pre-restore summary and confirmation), safety backups, single-suggestion-database backup/restore (merge or replace), suggestion-database reset, factory reset, and cross-instance import (merge or replace, typed-confirmation-gated) are implemented. Configurable scheduled backup execution is not. |
| FR-016 | Implement migration support | Not started | Required before persistent format changes become necessary. |

## Cross-Cutting Foundations

| Foundation | Status |
| --- | --- |
| WASH Crew fail-closed authorization | Complete |
| Guild scoping for implemented database operations | Complete |
| Discord timestamp formatting | Complete foundation |
| Diagnostics command | Complete foundation |
| Startup data-integrity checks | Complete foundation |
| Structured application logging | Complete foundation |
| Automated test suite | 569 passing tests |
