# Changelog

All notable changes to Watch Party Manager are documented in this file.

## [Unreleased]

### Added

- Watch Item and Watch Item Journey domain models with validation and normalization.
- Guild-scoped suggestion databases with JSON persistence.
- Suggestion creation, listing, removal, database association, and Discord message references.
- Intelligent nominee selection with configurable candidate counts.
- Voting rounds with blind and visible modes, configurable duration, candidate validation, vote-change limits, deterministic standings, and tie support.
- Interactive Discord voting controls and persistent view restoration after restart.
- `/ping`, `/version`, `/help`, `/add`, `/list`, `/remove`, `/start_vote`, `/vote_status`, `/vote`, `/stats`, `/diagnostics`, `/database_add`, `/database_list`, and `/database_remove`.
- WASH Crew role authorization using `WASH_CREW_ROLE_ID`, including fail-closed behavior when the role is not configured.
- Discord timestamp formatting for user-facing dates and times.
- Statistics service and guild-scoped statistics command.
- Diagnostics output, startup integrity checks, and configurable application logging.
- JSON repositories for suggestions, suggestion databases, and voting rounds.
- Comprehensive automated tests, with 569 tests passing at the current baseline.
- Initial product, architecture, functional, data-model, administration, developer, decision-log, glossary, and future-ideas documentation.

### Changed

- Renamed suggestion commands from `/suggest`, `/suggestions`, and `/remove_suggestion` to `/add`, `/list`, and `/remove`.
- Updated `/start_vote` to offer default or customized nominee count, duration, and visibility before creating a round.
- Updated user-facing voting status and help output.
- Strengthened guild scoping, validation, persistence safety, diagnostics, and error handling across implemented services.

### In Progress

- Automatic expiration and closing of voting rounds.
- Winner announcements and blind-vote standings reveal.
- Watch Item Journey updates when a voting round completes.
- Restart-safe vote completion.
