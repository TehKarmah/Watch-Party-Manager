# Watch Party Manager Project State

_Last Updated: July 2026_

This document is the authoritative summary of the current implementation status of Watch Party Manager (WPM). Update it whenever a feature or milestone is completed.

## Current Milestone

**Release Candidate UX & Consistency Audit**

Core functionality is implemented and tested; WASH is in release-candidate hardening. Recent work: **Rotation Removal (final phase)** -- the Rotation system has been deleted completely. `RotationService`, `RotationRepository`, `domain/rotation.py`, `RotationPoolStrategy`, `SoftRotationStrategy`, the Rotation Refresh Notification, Rotation statistics, Rotation configuration (including `SuggestionAdmissionMode`), Rotation persistence, and every other piece of Rotation-specific runtime logic no longer exist. The only supported nominee-selection modes are Pure Random, Favor New Additions, and Favor Older Additions; the only runtime suggestion states are Available, In an Active Vote, Vote Winner, Watched, and Retired. New suggestions become eligible immediately -- there is no admission delay or mode to configure. `CollectionEligibilityService` and `NomineeSelectionService` were already rotation-agnostic (from the preceding phase) and needed no further redesign; the Eligible Pool Warning (threshold-crossing dedup, `candidate_count × 5` default threshold) is unaffected. Old backups/configuration files with obsolete rotation-related keys (`rotation_pool`/`soft_rotation` candidate-selection values, `admission_mode`, `rotation_id`, `rotation_history`) continue to load gracefully -- those keys are simply ignored rather than causing a failure. Earlier work: "Candidate Selection" was renamed to "Nominee Selection" throughout the UI and documentation (internal class/file names are unchanged). Earlier still: a four-status suggestion model (Available/In an Active Vote/Vote Winner/Retired) with synchronized public-post status fields, a redesigned resumable `/setup` wizard (Home Channel step, collections defaulting to sibling threads under it), a standardized duration syntax (`10m`/`1h`/`7d`, an explicit unit always required; `w`/week(s) is also accepted but never advertised) shared by vote duration, reminder-before-close (now minute-precise), and `/voting edit`'s Shorten/Extend Vote, a full terminology/navigation/consistency audit pass, and (pre-release, breaking) a command structure cleanup: `/start_vote`/`/vote_status`/`/edit_vote` became `/voting start`/`/voting status`/`/voting edit`; `/database_add`/`/database_list`/`/database_backup`/`/database_restore`/`/database_reset`/`/database_remove` became `/database add`/`/database list`/`/database backup`/`/database restore`/`/database reset`/`/database remove`, alongside a new `/database move` and a modernized `/database add` (type choice, then a destination choice: Create New Thread, Use Current Thread/Channel, Use Existing Thread, or Use Existing Channel -- the same shared destination-selection flow `/database move` and the new guided `/database manage` command both reuse); and `/schedule_watch_party`/`/reschedule_watch_party`/`/cancel_watch_party`/`/watch_party_status` became `/watch-party schedule`/`/watch-party reschedule`/`/watch-party cancel`/`/watch-party status` (hyphenated, distinct from the pre-existing `/watch_party` membership-administration group). No compatibility aliases were kept.

## Last Completed Milestone

**Statistics & Reporting (FR-034)**

Server, member, suggestion, and database statistics are available through `/stats`, derived entirely from existing historical data (no running counters or caches), with privacy rules matching `/list`'s ephemeral-by-default, Crew-optional-public pattern -- plus a member-only exception allowing a member to publicly post their own statistics.

## Overall Completion Estimate

Core suggestion, voting, nominee selection, statistics, membership, setup/configuration, and backup/restore foundations are implemented and under automated test. Planned post-v1.0 scope is concentrated in the richer Event Series/Scheduled Event model, Discord Event publishing, retroactive watch-history correction, and configurable scheduled-backup execution -- see [Administration](05-Administration.md)'s "Planned Post-v1.0 Administration" section for specifics.

## Functional Requirement Status

| Area | Status | Notes |
| --- | --- | --- |
| Watch Item domain | Complete | Validation, normalized metadata, release year, submitter/creation-date tracking (for suggestions created since FR-034). |
| Watch Item Journey | Complete foundation | Rejection/retirement tracking, vote-win recording are wired into voting completion. Automatic "watched" marking is not yet produced by any code path (see Known Limitations). |
| Suggestion databases | Complete | Guild-scoped creation (type choice + Create New Thread/Use Existing Thread/Use Existing Channel destination choice), listing, moving a collection's suggestion destination, deactivation/reactivation, per-database configuration, and per-database backup/restore/reset. |
| Suggestions | Complete | Add, list (with filters, pagination, and archive browsing), edit, remove (archive-preferring), duplicate detection, IMDb link normalization, re-suggestion rules, and public confirmation posts. |
| Nominee selection | Complete | Pure Random, Favor New Additions (default), and Favor Older Additions are the only supported modes. New suggestions are eligible immediately regardless of mode -- there is no admission delay or mode to configure. |
| Voting engine | Complete | Blind/visible rounds, ballots, changes, standings, winners, and ties are implemented. Visible is the default visibility (Blind remains fully selectable and unchanged for existing guilds/rounds); a vote started without an explicit override uses the guild's configured default. Voting duration is minute-based (1 minute through 30 days), defaulting to 1 day, entered using WASH's standardized duration syntax (`10m`, `1h`, `7d` -- an explicit unit is always required); older hour- or day-based guild configurations load as the equivalent minute count automatically. `/voting edit`'s Change End Time offers End Now, Shorten Vote, Extend Vote (both relative to the round's current end time, via 1 Hour/1 Day/Custom...), and Set Exact End Time (a pasted Discord timestamp). |
| Interactive voting | Complete | Discord controls and persistent restoration after restart are implemented. The active-vote post is WASH's standard yellow-accent embed showing visibility, end time, and candidate titles (no leading nominee number); `/voting status` and standings resolve candidates to titles rather than internal suggestion numbers. |
| Vote completion | Complete | Automatic expiration, closing, winner announcements, and Watch Item Journey updates are implemented. |
| Statistics | Complete foundation | Server, member, suggestion, and database statistics are implemented. Likes, leaderboards, graphs, and exports are explicitly out of scope for the current architecture. |
| Diagnostics and integrity | Complete foundation | WASH Crew health/configuration/runtime diagnostics are shown via `/about`'s expanded sections (no separate `/diagnostics` command); startup checks and logging are implemented. |
| Membership | Complete | Self-service, manual, approval-required, and Discord-managed join modes; membership administration commands. |
| Scheduled watch parties | Complete foundation | Single-occurrence scheduling, rescheduling, cancellation, and reminders. The richer recurring Event Series/Discord Event model remains future work. |
| Setup and configuration | Complete | Guided, resumable `/setup` wizard (per-step Back navigation, Save & Finish Later, and resume-with-progress detection) and an always-available `/config` menu cover WASH Crew/Watch Party roles, a Home Channel, suggestion databases, voting/reminder/backup defaults, and nominee-selection mode -- setup and `/config` read and write the exact same persisted values. Collections default to threads created as siblings under the Home Channel (a descriptive default name for Movies/TV Shows, fully custom for other types), immediately usable by `/add` with no further configuration. |
| Backup, restore, import, reset | Complete | Full and per-database backup/restore, factory reset, and cross-instance import, each with pre-action safety backups. Automatic scheduled backup *execution* is not yet wired to the existing schedule/retention settings. |

## Implemented Discord Commands

Run `/help` in Discord for the exact, permission-scoped list available to a given user -- the list below groups every command by minimum required role. See [Administration](05-Administration.md) for behavior details.

### Everyone

- `/help`, `/about`, `/join_watch_party`

### Watch Party members (WASH Crew inherit these)

- `/add`, `/list`, `/stats`

### WASH Crew

- `/remove`, `/edit_suggestion`, `/repair_suggestions`
- `/voting start`, `/voting status`, `/voting edit`
- `/database add`, `/database manage`, `/database list`, `/database move`, `/database remove`, `/database backup`, `/database restore`, `/database reset`
- `/watch-party schedule`, `/watch-party reschedule`, `/watch-party cancel`, `/watch-party status`
- `/watch_party` (membership administration)
- `/setup`, `/config`
- `/backup`, `/restore`, `/factory_reset`, `/import`

## Implemented Services

- `SuggestionService`, `DuplicateDetectionService`, `SuggestionInputService`, `SuggestionRepairService`
- `VoteService`, `VoteCompletionService`, `NomineeSelectionService`
- Nominee selection strategies (Pure Random / Favor New Additions / Favor Older Additions), `CollectionEligibilityService`
- `StatisticsService`, `IntegrityService`
- `WatchPartyService`, `MembershipService`
- `SetupWizardService`, `ConfigService`
- `BackupService`, `DatabaseBackupService`, `ImportService`, `ResetService`, `RestoreSummaryService`
- `PermissionService`, `SchedulerService`
- `ImdbMetadataService`, `EligiblePoolWarningService`

## Persistence

JSON repositories for: suggestions, suggestion databases, per-database configuration, guild configuration, votes, watch parties, membership requests, setup wizard state, and scheduled jobs.

Discord voting views and suggestion "I WILL NOT WATCH" controls are restored after restart. Historical voting rounds, suggestions (archived rather than deleted by default), and membership requests are retained. `BackupService` sweeps every `*.json` file under `data/`, so any new repository is automatically covered by backup/restore without special-casing.

## Domain Models

- `WatchItem`, `WatchItemJourney`
- `SuggestionDatabase`, `SuggestionDatabaseConfiguration`
- `GuildConfiguration`
- `VoteRound`, `VoteRecord`, `VoteVisibility`, `VoteRoundStatus`
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
- A suggestion's five display statuses are Available, In an Active Vote, Vote Winner, Retired, and Watched -- `WatchItemStatus.VOTE_WINNER` is set automatically when a voting round completes; a group's actual viewing is separately confirmed through the watch-history workflow.
- Configurable scheduled-backup *execution* is not yet wired to the existing interval/retention settings (manual `/backup`, `/database backup`, and pre-destructive-action safety backups all work today).
- The richer Event Series/recurring-schedule/Discord Event model remains future work; scheduled watch parties today are single-occurrence.
- `SuggestionService`'s storage is keyed by `(database_id, normalized title)`, so two suggestions can never share an exactly-matching title within one database -- see [Administration](05-Administration.md)'s "Known limitation: identical titles within one database."
- Member/suggestion statistics that depend on a recorded submitter or creation date only cover suggestions added since FR-034 shipped; earlier suggestions are excluded rather than guessed at.
- Suggestions saved before public confirmation posts existed (or whose post failed at the time) have no original-post link for `/list` to show, and `/repair_suggestions` cannot recover it -- Discord provides no reliable way to relocate a message after the fact without inventing a URL or risking a duplicate public post.
- `CHANGELOG.md`'s `[1.0.0]` entry is the release record; `[Unreleased]` tracks the release-candidate hardening work (suggestion status model, setup wizard redesign, vote editing redesign, duration syntax standardization) done since.

## Next Recommended Milestone

With core functionality, configuration, and documentation in place, planned post-v1.0 work is concentrated in: automatic scheduled-backup execution, the Event Series/Discord Event scheduling model, and retroactive watch-history correction. See [Administration](05-Administration.md)'s "Planned Post-v1.0 Administration" section.

## Testing Status

- Full automated suite passing
- Current baseline: **2850 tests**
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
- Current package version: `1.0.0`
