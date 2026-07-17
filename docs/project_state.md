# Watch Party Manager Project State

_Last Updated: July 16, 2026_

This document is the authoritative summary of the current implementation status of Watch Party Manager (WPM). Update it whenever a feature or milestone is completed.

## Current Milestone

**Voting Lifecycle Completion**

The voting foundation and interactive voting experience are implemented. The active milestone is automatic vote expiration, completion, winner announcement, and Watch Item Journey updates.

## Last Completed Milestone

**Interactive Voting and Start-Vote Customization**

The completed work includes persistent Discord voting controls, restart restoration, intelligent nominee selection, and a `/start_vote` flow that supports defaults or per-round customization.

## Overall Completion Estimate

Approximately **45% of the planned Version 1 scope** is implemented. Core suggestion and voting foundations are mature, while scheduling, Discord Events, reminders, watch history workflows, setup, backup and restore, and broader administration remain future work.

## Functional Requirement Status

| Area | Status | Notes |
| --- | --- | --- |
| Watch Item domain | Complete | Validation and normalized metadata are implemented. |
| Watch Item Journey | Complete foundation | Model exists; automatic lifecycle updates are pending. |
| Suggestion databases | Complete foundation | Guild-scoped creation, listing, removal, and activation behavior are implemented. |
| Suggestions | Complete foundation | Add, list, remove, persistence, database association, and post references are implemented. |
| Nominee selection | Complete foundation | Guild-scoped selection and candidate-count validation are implemented. |
| Voting engine | Complete foundation | Blind/visible rounds, ballots, changes, standings, winners, and ties are implemented. |
| Interactive voting | Complete | Discord controls and persistent restoration are implemented. |
| Vote completion | In progress | Expiration, closing, announcements, and journey updates are the current task. |
| Statistics | Partial | Initial read-only guild statistics are implemented. Expanded historical reporting remains future work. |
| Diagnostics and integrity | Complete foundation | Crew diagnostics, startup checks, and logging are implemented. |
| Scheduling and Discord Events | Not started | Planned after voting lifecycle and watch history foundations. |
| Backup, restore, import, and export | Not started | Planned administration milestone. |
| Setup and configuration UI | Not started | Current configuration uses environment variables. |

## Implemented Discord Commands

### Community

- `/ping`
- `/version`
- `/help`
- `/add`
- `/list`
- `/remove`
- `/vote`
- `/vote_status`
- `/stats`

### WASH Crew

- `/start_vote`
- `/database_add`
- `/database_list`
- `/database_remove`
- `/diagnostics`

## Implemented Services

- `SuggestionService`
- `VoteService`
- `NomineeSelectionService`
- `StatisticsService`
- `IntegrityService`

## Persistence

- JSON Suggestion Repository
- JSON Suggestion Database Repository
- JSON Vote Repository
- Persistent Discord voting-view restoration

Completed voting rounds are retained by the vote repository. Automatic archiving behavior is part of the current milestone.

## Domain Models

- `WatchItem`
- `WatchItemJourney`
- `SuggestionDatabase`
- `VoteRound`
- `VoteRecord`
- `VoteVisibility`
- `VoteRoundStatus`

## Current Configuration

- `DISCORD_TOKEN`
- `DISCORD_GUILD_ID`
- `WASH_CREW_ROLE_ID`
- `DEFAULT_VOTE_NOMINEE_COUNT`

Restricted commands fail closed when `WASH_CREW_ROLE_ID` is not configured.

## Architecture Rules

- Domain models own validation and business rules.
- Services coordinate application logic.
- Discord commands and views remain thin.
- Repository classes isolate persistence.
- Configuration is preferred over hardcoded community policy.
- Discord objects do not enter the domain layer.
- Guild-owned data and operations must remain guild-scoped.
- Historical records should be preserved rather than destructively replaced.

## Known Technical Debt and Limitations

- JSON is the current persistence layer; the specification still anticipates a future migration path to a more scalable database.
- Setup and routine configuration are not yet available through Discord.
- `/remove` currently operates through the service API without the final database-selection user experience.
- Voting rounds do not yet close automatically or announce winners.
- Watch history and automatic Watch Item Journey updates are not yet wired into voting completion.
- Backup, restore, import, export, and migration tooling are not implemented.

## Next Recommended Milestone

Complete the voting lifecycle, then implement the watch-history foundation that records completed selections and prepares scheduling work.

## Testing Status

- Full automated suite passing
- Current baseline: **569 tests**
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
