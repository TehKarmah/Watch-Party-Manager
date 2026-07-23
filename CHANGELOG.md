# Changelog

All notable changes to Watch Party Manager are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Planned

- Final v1.0 release polish and sign-off.
- Release validation against the [Installation Guide](docs/09-Installation-Guide.md)'s verification checklist on a clean environment.
- A final documentation review pass before tagging the release.

## [1.0.0] - Release Candidate

Watch Party Manager's first complete, self-hostable release: a Discord bot (**WASH**, the Watch Party Administration & Scheduling Helper) that runs a community's entire watch-party lifecycle -- suggesting titles, voting, scheduling, and reporting on the results -- through guided setup rather than manual configuration file editing.

### Added

**Foundation**

- Core domain models: `WatchItem`, `WatchItemJourney`, `SuggestionDatabase`, `VoteRound`/`VoteRecord`, `GuildConfiguration`, `Rotation`, `WatchParty`, and `MembershipRequest`, each with its own validation and a dedicated JSON repository.
- A layered architecture separating domain models, persistence, Discord-agnostic services, and thin Discord command/view code, enabling the test suite to exercise business logic independently of Discord.

**Suggestion Management**

- `/add`, `/list`, `/edit_suggestion`, and `/remove` for the full suggestion lifecycle, plus `/repair_suggestions` for correcting malformed legacy entries.
- IMDb link normalization (accepting common URL variants and storing a canonical form) alongside plain-title suggestions -- no IMDb link is ever required.
- Duplicate detection distinguishing definite duplicates (matching IMDb ID or title-and-year) from possible duplicates (matching title, unknown year), checked against active, archived, and watched items.
- Re-suggestion rules: active duplicates are always blocked; archived, rejected, or previously-watched matches require WASH Crew confirmation to reactivate rather than creating a second record.
- Archive-preferring removal: `/remove` (and the automatic rejection-threshold path) archive a suggestion rather than deleting it, preserving its history and reference number.
- Public confirmation posts announcing a newly added or reactivated suggestion in its database's configured channel.
- Stable, zero-padded reference numbers (`#0007`) for every suggestion, usable when removing, editing, or looking up statistics.

**Multi-Database & Guild Configuration**

- Guild-scoped suggestion databases (`/database_add`, `/database_list`, `/database_remove`), each tied to its own Discord channel or thread and independently configurable.
- Per-database configuration covering suggestion rules, voting overrides, notifications, permissions, archive behavior, and watch-history settings.
- Guild-wide configuration (roles, channels, voting defaults, notifications, feature flags, backup schedule) persisted independently of environment variables.

**Setup Wizard & Configuration**

- A guided, fully re-runnable `/setup` wizard covering the WASH Crew role, the Watch Party role and join mode, suggestion database selection or creation, watch-history destination, voting defaults, reminder defaults, and backup defaults, ending in a save/edit/cancel summary.
- `/config`, an always-available menu mirroring the wizard's sections for making individual changes afterward without repeating the whole flow.

**Voting System**

- `/start_vote` with a choice of guild-configured defaults or per-round customization (nominee count, duration, visibility).
- Blind and visible voting, selectable per round or as a guild default.
- Interactive Discord voting posts with numbered vote buttons, vote changes within a configurable limit, and persistent restoration of every interactive control after a bot restart.
- Deterministic standings and winner calculation with tie support, plus `/vote_status` and WASH Crew's `/edit_vote` (change end time, end early, or cancel).
- Fully automatic vote completion: expiration, closing, winner announcement, and Watch Item Journey updates, driven by a persistent scheduler rather than requiring a WASH Crew member to close a round manually.

**Candidate Selection & Rotation Management**

- Configurable candidate-selection strategies per database: Rotation Pool (the default, excluding a suggestion from selection once presented until a fresh rotation begins), Soft Rotation (weighting presented suggestions lower without excluding them), and Infinite Pool (no rotation restriction at all).
- Persistent rotation lifecycle tracking -- assigned, presented, remaining, retired, and watched counts, with completion percentage -- and automatic transition to a fresh rotation once one is exhausted.
- Configurable admission for newly added suggestions: join the next rotation (the default) or expand the current one immediately.
- A distinct "retired" lifecycle for suggestions archived via the "I WILL NOT WATCH" rejection threshold, separate from WASH Crew-initiated archival.
- An optional Low Pool Reminder that nudges a channel when a database's active suggestion count falls below a configurable threshold, rate-limited to a configurable minimum interval.
- Genre- and media-type-diversity-aware nominee selection that deprioritizes (without ever permanently excluding) recently nominated or recently won suggestions.

**Statistics & Reporting**

- `/stats` with server, member, suggestion, rotation, and database views, all recalculated from historical data on every request rather than maintained as running counters.
- Server statistics covering watch parties, voting rounds, blind/visible and tie counts, average participation, average candidates per round, and average vote duration.
- Per-suggestion statistics: created date, submitter, nomination history, watch/retirement status, and rotations participated in.
- Per-member statistics restricted to the requesting member's own history (suggestions submitted/watched/retired, votes cast, participation percentage, winning suggestions) -- no one, including WASH Crew, can retrieve another member's personal statistics.

**Backup, Restore & Import**

- `/backup` and `/restore` (selecting an existing local backup or uploading one) for a full server snapshot, with pre-restore validation, a change summary, and confirmation before anything is overwritten.
- Automatic safety backups before every destructive operation.
- Per-database `/database_backup` and `/database_restore` (merge or replace), plus `/database_reset` and a fully typed-confirmation-gated `/factory_reset`.
- `/import`, for merging or replacing data from another WASH instance's backup archive, with conflict reporting.

**Permissions, Roles & Membership**

- Two-tier, fail-closed role permissions: WASH Crew (administrative) and Watch Party member (participant), with WASH Crew automatically inheriting member permissions.
- Four Watch Party join modes -- Self-Service (`/join_watch_party`), Manual, Approval-Required, and Discord-Managed -- plus `/watch_party` for WASH Crew to list, approve/deny, manually add or remove, and search membership history.
- Ephemeral-by-default command output throughout, with WASH Crew able to post eligible results (suggestion lists, statistics) publicly, and members able to publicly post their own statistics.

**Scheduling**

- A persistent, restart-safe job scheduler driving automatic vote closing, vote-ending reminders, and watch-party reminders.
- Scheduled watch parties (`/schedule_watch_party`, `/reschedule_watch_party`, `/cancel_watch_party`, `/watch_party_status`) with configurable reminders.

**Help, About & Diagnostics**

- A structured, permission-aware `/help` registry showing each member only the commands they can use, linking out to expanded GitHub-hosted documentation.
- `/about`, reporting version, build, Discord gateway latency, and uptime alongside a feature summary.
- `/diagnostics` (WASH Crew only), startup data-integrity checks, and structured application logging throughout.

### Changed

- Renamed the original suggestion commands (`/suggest`, `/suggestions`, `/remove_suggestion`) to `/add`, `/list`, and `/remove`.
- Replaced the original `/version` command with the richer `/about`.
- Replaced direct IMDb scraping with OMDb API-backed metadata resolution (optional, configured via `OMDB_API_KEY`); plain-title suggestions have never required it.
- Moved the full command reference out of Discord messages and into linked GitHub documentation, keeping `/help` itself concise.
- Broadened `/list` and `/stats` from WASH-Crew-only to every Watch Party member, with public posting remaining WASH Crew-gated (except a member's own statistics).

### Improved

- Suggestion and database list formatting, with Discord-safe pagination for lists too long for a single message.
- Voting and suggestion post presentation, including numbered vote buttons and persistent "I WILL NOT WATCH" controls.
- Statistics and diagnostics output clarity.
- Guild scoping, input validation, and persistence safety strengthened across every service as the project matured.

### Fixed

- Suggestion list entries no longer conflate a Discord post link with the watch item's title.
- Watch item reference numbers are now stable and consistently formatted everywhere they appear.

### Documentation

- Full documentation set covering product vision, architecture, functional specification, data model, administration, developer guide, decision log, glossary, and future ideas.
- A guild configuration specification detailing every persisted setting the Setup Wizard and `/config` can change.
- A complete [Installation Guide](docs/09-Installation-Guide.md) for first-time setup: Python environment setup, Discord Developer Portal and bot creation, required permissions and intents, `.env` configuration, OMDb API configuration, the Setup Wizard walkthrough, an installation verification checklist, and troubleshooting.
- README, project state, implementation checklist, and administration/developer guides refreshed to match the current implementation.

### Testing

- Automated test suite grown to **2,426 passing tests**, covering domain validation, service behavior, repository persistence and migration, permission fail-closed behavior, guild scoping, and restart-safe restoration of every persistent Discord view.
