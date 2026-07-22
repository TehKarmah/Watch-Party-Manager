# Watch Party Manager

## Administration

| Property | Value |
| --- | --- |
| Document | Administration |
| File | `05-Administration.md` |
| Version | 1.0 Draft |
| Status | Active Draft |
| Last Updated | July 2026 |
| Authors | TehKarmah & ChatGPT |

> [!NOTE]
> This document distinguishes administration available in the current 0.1.0 development build from the broader Version 1 administration plan.

## 1. Current Administrative Model

WASH currently uses a configured Discord role named **WASH Crew** for restricted operations. Set the role ID with `WASH_CREW_ROLE_ID`.

Restricted commands fail closed. When no WASH Crew role is configured, no user can run those commands.

Current WASH Crew commands:

- `/start_vote`
- `/database_add`
- `/database_list`
- `/database_remove`
- `/diagnostics`

The final setup wizard and broader Discord-based configuration system are not yet implemented.

## 2. Environment Configuration

Copy `env.example` to `.env` and configure the values needed for the installation.

| Setting | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Authenticates the Discord bot. |
| `DISCORD_GUILD_ID` | No | Synchronizes commands to one development guild for faster testing. |
| `WASH_CREW_ROLE_ID` | Strongly recommended | Authorizes restricted administration commands. |
| `DEFAULT_VOTE_NOMINEE_COUNT` | No | Sets the default nominee count from 2 through 10. The default is 3. |

Do not commit the populated `.env` file.

## 3. Suggestion Database Administration

Suggestion databases organize Watch Items within a Discord server and channel context.

### Create a database

Use `/database_add` and provide a name. Database names must be valid under the service's normalization and duplicate-name rules.

### List databases

Use `/database_list` to review databases available to the current guild.

### Remove a database

Use `/database_remove` with its database ID. The command applies the repository's safety and ownership validation.

Database operations are guild-scoped. A guild must not access or change another guild's databases.

## 4. Starting a Vote

Use `/start_vote` to begin an interactive setup flow.

WASH offers:

- **Use Defaults**, which applies the configured nominee count, seven-day duration, and default visibility.
- **Customize This Vote**, which accepts a nominee count, duration from 1 through 30 days, and blind or visible voting.

WASH selects nominees from the applicable suggestion database and creates an interactive voting post. Candidate availability is validated before the round is created.

Only one open round is supported by the current voting service behavior.

## 5. Voting Operations

Community members can vote through the interactive post. The `/vote` command remains available for ID-based voting, and `/vote_status` reports the current round.

Current voting capabilities include:

- Blind or visible voting
- Configurable duration
- Vote-change limits
- Deterministic standings
- Tie support
- Persistent round storage
- Persistent interactive controls after restart

Automatic expiration, closing, and winner announcements are currently in development.

## 6. Diagnostics and Integrity

Use `/diagnostics` to review operational information such as:

- Application, Python, and discord.py versions
- Latency and uptime
- Guild-scoped data summaries
- Interactive voting restoration state

WASH also runs integrity checks against persisted data and writes operational information through the logging system. Review console and log output when startup reports an issue.

## 7. Data Storage

The current development build stores application data in JSON repositories for:

- Suggestion databases
- Suggestions and Watch Items
- Voting rounds

Historical voting rounds are retained. Direct JSON editing is not recommended because invalid or cross-referenced data can break application behavior.

Before manual maintenance, stop the bot and make a copy of the data files.

## 8. Current Maintenance Procedure

1. Confirm the full automated test suite passes before deployment.
2. Stop the running bot.
3. Back up the data directory manually (or run `/backup` beforehand).
4. update the checked-out repository.
5. Activate the virtual environment and install updated dependencies if needed.
6. Start WASH and review startup logs.
7. Run `/diagnostics` from an authorized account.
8. Smoke-test any commands changed in the release.

Import/export between separate WASH instances and configurable scheduled backup execution are planned but not implemented (see Section 9).

## 9. Backup & Recovery

WASH Crew can create, validate, and restore backups directly from Discord. Every backup is a checksummed `.zip` containing a `manifest.json` plus the relevant JSON data files.

### Manifest

Every backup's `manifest.json` records:

- `project_name` -- always "Watch Party Manager" (the project's own name, not WASH's Discord-facing name).
- `application_version` -- the running WASH version that created the backup.
- `format_version` / `backup_format_version` -- the archive's structural format version, used to reject backups from an incompatible future version of WASH.
- `backup_type` -- `full` (the whole data directory) or `suggestion_database` (one database only).
- `kind` -- `manual` or `scheduled`, which retention pool the archive counts against.
- `created_at`, `guild_id` -- when and (informationally) which server the backup was made from.
- `database_id` / `database_name` -- present only on a `suggestion_database`-type backup.
- `files` -- every included file's relative path, size, and SHA-256 checksum.

Backups created before this manifest existed are still accepted: any field it doesn't recognize is simply reported as unavailable rather than treated as an error.

### `/backup`

Creates an immediate manual backup of WASH's entire data directory, attaches it to the response as `Watch_Party_Manager_Backup_YYYY-MM-DD_HH-MM-SS.zip`, and reports its filename, creation time, and type. Responses are ephemeral. WASH Crew only.

### `/restore`

Restores WASH's entire dataset. The flow always is: select an existing local backup by filename **or** upload a `.zip` -> WASH validates it and shows a summary (application version, creation time, backup type, guild ID, and whichever record counts it can determine -- suggestion databases, suggestions, vote rounds, membership requests, and whether a guild configuration is present) -> WASH Crew explicitly clicks **Restore** or **Cancel**. Nothing is ever restored without that explicit confirmation, and validation never modifies live data.

Immediately before restoring, WASH creates a full safety backup of the current data using the same backup process. If that safety backup fails, the restore is aborted and live data is left untouched. If the restore step itself fails afterward, the safety backup is preserved and the failure message says so explicitly.

**A bot restart is recommended after any restore.** Several in-memory caches (suggestions, votes, membership requests) are only loaded once at startup; restored data on disk won't be reflected in a running bot's behavior until it restarts. `GuildConfiguration` reads are not cached and take effect immediately.

### `/database_backup` and `/database_restore`

Back up or restore a single suggestion database instead of everything. `/database_backup database_id:<id>` produces a scoped backup containing only that database's record, its suggestions, and its configuration (not its vote history), attached as `Watch_Party_Manager_Database_Backup_<safe-database-name>_YYYY-MM-DD_HH-MM-SS.zip`.

`/database_restore` requires choosing **Merge** or **Replace** explicitly -- WASH never infers which one you meant:

- **Merge** imports suggestions from the backup into the *existing* database with a matching ID. A suggestion whose title already exists for that database is skipped and reported as a conflict rather than overwritten. The destination database must already exist; Merge never creates one.
- **Replace** overwrites the selected database's own record and all of its suggestions with the backup's version (creating it fresh if it no longer exists), while leaving every other database and all other guild data untouched. A full safety backup is made first, exactly as with `/restore`.

A single-database backup can only be restored back into the guild it came from; WASH rejects a mismatch rather than silently importing another server's data.

### Troubleshooting

| Symptom | Cause | What happened to live data |
| --- | --- | --- |
| "This backup failed validation and cannot be restored" | Corrupt ZIP, missing/unreadable manifest, unsafe path, or a checksum mismatch (tampered or truncated file). | Unchanged -- validation never writes anything. |
| "Unsupported backup type" | A full backup was offered to `/database_restore`, or a single-database backup was offered to `/restore`. | Unchanged. |
| "That backup was created in a different Discord server" | A `/database_backup` archive's recorded guild ID doesn't match the server `/database_restore` was run in. | Unchanged. |
| "No existing suggestion database with that ID was found to merge into" | Merge was chosen but the destination database doesn't exist yet. | Unchanged -- use Replace instead if that's intended. |
| "N suggestion(s) were skipped as duplicates" | Merge detected a title already present in the destination database. | Only the non-conflicting suggestions were imported; nothing existing was overwritten. |
| "Safety backup failed, so the restore was aborted" | WASH couldn't write the pre-restore safety backup (e.g. disk full or permissions). | Unchanged -- the restore never began. |
| "Restore failed after the safety backup succeeded" | The safety backup was made, but copying the backup's files onto live data failed partway through. | The safety backup archive is intact and named in the error message; use `/restore` again with it if needed. |

## 10. Planned Version 1 Administration

The Version 1 plan includes:

- Guided setup and rerunnable configuration
- Existing, newly created, or deferred watch-history destinations
- Rotation and event-series administration
- Scheduling and Discord Event publishing
- Historical corrections and retroactive watch-history entry
- Cross-instance import and export
- Configurable scheduled backup execution (the retention/interval settings already exist in `/config`; the scheduler does not yet act on them)
- Health and maintenance reporting

Until those features are implemented, `project_state.md` is authoritative about what administrators can use safely.
