# Watch Party Manager Project State

_Last Updated: July 2026_

This document is the authoritative summary of the current implementation status of Watch Party Manager (WPM). Update it whenever a feature or milestone is completed.

## Current Milestone

**Documentation Audit & Installation Guide (FR-035)**

Core functionality (suggestions, voting, rotation, statistics, membership, setup, backup/restore) is implemented and tested. The active work is a release-quality documentation pass: a first-time-user Installation Guide, and correcting documentation that had drifted from the implementation as features shipped.

## Last Completed Milestone

**Statistics & Reporting (FR-034)**

Server, member, suggestion, rotation, and database statistics are available through `/stats`, derived entirely from existing historical data (no running counters or caches), with privacy rules matching `/list`'s ephemeral-by-default, Crew-optional-public pattern -- plus a member-only exception allowing a member to publicly post their own statistics.

## Overall Completion Estimate

Core suggestion, voting, rotation, statistics, membership, setup/configuration, and backup/restore foundations are implemented and under automated test. Remaining Version 1 scope is concentrated in the richer Event Series/Scheduled Event model, Discord Event publishing, retroactive watch-history correction, and configurable scheduled-backup execution -- see [Administration](05-Administration.md)'s "Planned Version 1 Administration" section for specifics.

## Functional Requirement Status

| Area | Status | Notes |
| --- | --- | --- |
| Watch Item domain | Complete | Validation, normalized metadata, release year, submitter/creation-date tracking (for suggestions created since FR-034). |
| Watch Item Journey | Complete foundation | Rotation history, rejection/retirement tracking, vote-win recording are wired into voting completion. Automatic "watched" marking is not yet produced by any code path (see Known Limitations). |
| Suggestion databases | Complete | Guild-scoped creation, listing, deactivation/reactivation, per-database configuration, and per-database backup/restore/reset. |
| Suggestions | Complete | Add, list (with filters, pagination, and archive browsing), edit, remove (archive-preferring), duplicate detection, IMDb link normalization, re-suggestion rules, and public confirmation posts. |
| Candidate selection & rotation | Complete | Rotation Pool (default), Soft Rotation, and Infinite Pool selection strategies; persistent rotation lifecycle tracking; configurable new-suggestion admission modes; Low Pool Reminder. |
| Voting engine | Complete | Blind/visible rounds, ballots, changes, standings, winners, and ties are implemented. |
| Interactive voting | Complete | Discord controls and persistent restoration after restart are implemented. |
| Vote completion | Complete | Automatic expiration, closing, winner announcements, and Watch Item Journey updates are implemented. |
| Statistics | Complete foundation | Server, member, suggestion, rotation, and database statistics are implemented. Likes, leaderboards, graphs, and exports are explicitly out of scope for the current architecture. |
| Diagnostics and integrity | Complete foundation | WASH Crew health/configuration/runtime diagnostics are shown via `/about`'s expanded sections (no separate `/diagnostics` command); startup checks and logging are implemented. |
| Membership | Complete | Self-service, manual, approval-required, and Discord-managed join modes; membership administration commands. |
| Scheduled watch parties | Complete foundation | Single-occurrence scheduling, rescheduling, cancellation, and reminders. The richer recurring Event Series/Discord Event model remains future work. |
| Setup and configuration | Complete | Guided, resumable `/setup` wizard (per-step Back navigation, Save & Finish Later, and resume-with-progress detection) and an always-available `/config` menu cover WASH Crew/Watch Party roles, suggestion databases, voting/reminder/backup defaults, and candidate-selection mode -- setup and `/config` read and write the exact same persisted values. |
| Backup, restore, import, reset | Complete | Full and per-database backup/restore, factory reset, and cross-instance import, each with pre-action safety backups. Automatic scheduled backup *execution* is not yet wired to the existing schedule/retention settings. |

## Implemented Discord Commands

Run `/help` in Discord for the exact, permission-scoped list available to a given user -- the list below groups every command by minimum required role. See [Administration](05-Administration.md) for behavior details.

### Everyone

- `/help`, `/about`, `/join_watch_party`

### Watch Party members (WASH Crew inherit these)

- `/add`, `/list`, `/stats`

### WASH Crew

- `/remove`, `/edit_suggestion`, `/repair_suggestions`
- `/start_vote`, `/vote_status`, `/edit_vote`
- `/database_add`, `/database_list`, `/database_remove`, `/database_backup`, `/database_restore`, `/database_reset`
- `/schedule_watch_party`, `/reschedule_watch_party`, `/cancel_watch_party`, `/watch_party_status`
- `/watch_party` (membership administration)
- `/setup`, `/config`
- `/backup`, `/restore`, `/factory_reset`, `/import`

## Implemented Services

- `SuggestionService`, `DuplicateDetectionService`, `SuggestionInputService`, `SuggestionRepairService`
- `VoteService`, `VoteCompletionService`, `NomineeSelectionService`
- `RotationService`, candidate selection strategies (Rotation Pool / Soft Rotation / Infinite Pool)
- `StatisticsService`, `IntegrityService`
- `WatchPartyService`, `MembershipService`
- `SetupWizardService`, `ConfigService`
- `BackupService`, `DatabaseBackupService`, `ImportService`, `ResetService`, `RestoreSummaryService`
- `PermissionService`, `SchedulerService`
- `ImdbMetadataService`, `LowPoolReminderService`

## Persistence

JSON repositories for: suggestions, suggestion databases, per-database configuration, guild configuration, votes, rotations, watch parties, membership requests, setup wizard state, and scheduled jobs.

Discord voting views and suggestion "I WILL NOT WATCH" controls are restored after restart. Historical voting rounds, suggestions (archived rather than deleted by default), and membership requests are retained. `BackupService` sweeps every `*.json` file under `data/`, so any new repository is automatically covered by backup/restore without special-casing.

## Domain Models

- `WatchItem`, `WatchItemJourney`
- `SuggestionDatabase`, `SuggestionDatabaseConfiguration`
- `GuildConfiguration`
- `VoteRound`, `VoteRecord`, `VoteVisibility`, `VoteRoundStatus`
- `Rotation`, `RotationStatus`
- `WatchParty`, `WatchPartyStatus`
- `MembershipRequest`

## Current Configuration

Most server-specific behavior is configured per-guild through `/setup` and `/config` (persisted as `GuildConfiguration`/`SuggestionDatabaseConfiguration`), not through environment variables. Environment variables remain for bot-level and pre-setup bootstrap concerns:

- `DISCORD_TOKEN` (required)
- `DISCORD_GUILD_ID` (optional, faster command sync during development)
- `WASH_CREW_ROLE_ID` (optional -- can also be set via `/setup`)
- `WATCH_PARTY_MEMBER_ROLE_ID` (optional -- can also be set via `/setup`)
- `DEFAULT_VOTE_NOMINEE_COUNT` (optional)
- `OMDB_API_KEY` (optional, enables IMDb-link metadata resolution)

Restricted commands fail closed when the relevant role is not configured by either method. See the [Installation Guide](09-Installation-Guide.md) for the full setup walkthrough.

## Architecture Rules

- Domain models own validation and business rules.
- Services coordinate application logic.
- Discord commands and views remain thin.
- Repository classes isolate persistence.
- Configuration is preferred over hardcoded community policy.
- Discord objects do not enter the domain layer.
- Guild-owned data and operations must remain guild-scoped.
- Historical records should be preserved rather than destructively replaced.
- Statistics are always derived from historical data, never maintained as running counters.

## Known Technical Debt and Limitations

- JSON is the current persistence layer; the specification still anticipates a future migration path to a more scalable database.
- `WatchItemStatus.WATCHED` and `journey.record_watch_date()` are not yet produced by any code path -- watch-history and "watched" statistics are correctly implemented and tested but will show no results until a future watch-history milestone marks items watched.
- Configurable scheduled-backup *execution* is not yet wired to the existing interval/retention settings (manual `/backup`, `/database_backup`, and pre-destructive-action safety backups all work today).
- The richer Event Series/recurring-schedule/Discord Event model remains future work; scheduled watch parties today are single-occurrence.
- `SuggestionService`'s storage is keyed by `(database_id, normalized title)`, so two suggestions can never share an exactly-matching title within one database -- see [Administration](05-Administration.md)'s "Known limitation: identical titles within one database."
- Member/suggestion statistics that depend on a recorded submitter or creation date only cover suggestions added since FR-034 shipped; earlier suggestions are excluded rather than guessed at.
- `CHANGELOG.md`'s `[Unreleased]` section predates most milestones completed this cycle and has not been refreshed as part of this documentation pass.

## Next Recommended Milestone

With core functionality, configuration, and documentation in place, remaining Version 1 work is concentrated in: automatic scheduled-backup execution, the Event Series/Discord Event scheduling model, and retroactive watch-history correction. See [Administration](05-Administration.md)'s "Planned Version 1 Administration" section.

## Testing Status

- Full automated suite passing
- Current baseline: **2426 tests**
- Test framework: `unittest`
- Python version: 3.12

PowerShell:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Repository Notes

- Primary branch: `main`
- Source of truth: GitHub repository
- Development environment: VS Code
- Current package version: `0.1.0`
