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

### Adding suggestions

`/add title:<text> [imdb_url] [release_year]` accepts a plain title, an IMDb link (either pasted into `title` directly or given separately via `imdb_url`), and an optional release year. Any Watch Party member may use it.

**IMDb link normalization.** A supplied IMDb link is validated against common IMDb title URL variants (with or without `https://`/`www.`, with or without a trailing path/query) and stored in its canonical form: `https://www.imdb.com/title/tt1234567/`. A malformed link is rejected with a clear error before anything is saved. This normalization never contacts IMDb or any external service. Separately, and unrelated to this normalization step, `/add` also resolves basic metadata (runtime, genres, poster, etc.) through the OMDb API when `OMDB_API_KEY` is configured -- that lookup is pre-existing behavior this milestone did not change.

**Duplicate detection.** Before a suggestion is saved, WASH checks the target database's active, archived, and watched items for a match:

- An **IMDb ID match**, or a **matching title and release year**, is a *definite* duplicate.
- A matching title where either side's release year is unknown is a *possible* duplicate -- WASH never guesses.

What happens next depends on the matched item's status and who's asking:

| Matched item's status | Regular Watch Party member | WASH Crew |
| --- | --- | --- |
| Active (on the list already) | Blocked. Reference, title, IMDb link, and status are shown. | Blocked -- there is nothing to reactivate. |
| Archived (rejected via "I WILL NOT WATCH") | Blocked. | May confirm to reactivate the existing record. |
| Watched | Blocked. | May confirm to reactivate the existing record. |
| Archived some other way (e.g. via `/remove`) | Blocked. | May confirm to reactivate the existing record. |
| Possible duplicate (no confirmed year) | Blocked. | May confirm to proceed with a new suggestion. |

Reactivating always reuses the existing record's stable ID and full history (rejections, watch dates, vote appearances) rather than creating a second entry -- nothing is ever silently overwritten.

**Confirmation posts.** The command's own acknowledgment is always ephemeral. If the target database has a suggestion channel configured, WASH posts (or, for a reactivation, updates) a public confirmation there showing the title, release year, canonical IMDb link, and reference number. If no suggestion channel is configured, the suggestion is still saved and the ephemeral reply explains that no public post was made. If posting fails (permissions, deleted channel, etc.), the suggestion is still preserved and the ephemeral reply explains the failure.

### Listing suggestions

`/list [status] [public]` is available to every Watch Party member. `status` selects **Active** (default), **Archived**, **Watched**, or **All**. Only WASH Crew may set `public:true` to post the list in the channel; everyone else always sees it privately, including Archived and Watched.

Database selection follows the same automatic-then-selector pattern used elsewhere: the current channel's configured database is used automatically; if none matches and the guild has exactly one active database, that one is used; if several exist, WASH shows a picker. Each entry shows its reference number, title, release year (when known), IMDb link (when known), a link back to the original suggestion post (when known), and current status. Long lists page with Previous/Next buttons rather than being cut off or capped.

### Removing suggestions

`/remove query:<text>` is WASH Crew only. `query` may be a reference number (`#0007` or `7`), an exact title, or a title with its trailing "(YYYY)" year omitted. One match asks for confirmation before acting; several matches show a picker listing each candidate's reference, title, year, database, and status; no match reports that clearly. Confirmed removals **archive** the suggestion (its identity and full history are preserved) rather than deleting it -- see "Known limitations" for the one case where this isn't yet true everywhere.

### Editing suggestions

`/edit_suggestion reference:<text> [title] [release_year] [imdb_url] [database_id]` is WASH Crew only. `reference` is matched the same way `/remove` matches (reference number or exact title); any field left blank keeps its current value. A supplied IMDb link is normalized the same way `/add` normalizes one. Moving a suggestion to another database requires that database to exist, be active, and belong to the same guild. Whenever the title, release year, IMDb link, or database changes, the same duplicate check `/add` uses runs again against the destination database (excluding the suggestion's own record) -- a definite duplicate blocks the edit, a possible one requires confirmation. The stable ID, journey, and history are always preserved; only the edited fields (and an internal "last updated" timestamp) change.

### Known limitation: identical titles within one database

A "possible duplicate" warning is only ever raised because a candidate's title already matches an existing item's title (that's what makes it a candidate). Suggestion storage has always been keyed by (database, normalized title), so two records can never share an exactly-matching title in the same database. In practice this means confirming "add/save anyway" on a possible-duplicate warning succeeds only when the new title differs at all from every matched title -- confirming with a byte-for-byte identical title still reports the pre-existing "a suggestion with that title already exists" message instead of creating a second record. Changing this would mean changing how suggestions are identified in storage, which this milestone intentionally leaves alone.

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

Configurable scheduled backup execution is planned but not implemented (see Section 9) -- backups still require an explicit `/backup` today. Import from another WASH instance is implemented via `/import`; that other instance's own `/backup` output is the "export" side of the exchange, so there is no separate export command.

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

### `/database_reset`

Clears every suggestion (active and archived alike -- there is no separate archive store; both are just `WatchItem` records in the same file) from one suggestion database. The database record itself, its ID, its name, and its configuration are never touched, and no other database is affected.

Flow: select the database (`/database_reset database_id:<id>`) -> WASH shows how many suggestions would be removed -> click **Reset** -> a modal asks you to type `RESET` exactly (case-sensitive) -> WASH creates a full safety backup, then performs the reset. Clicking **Cancel**, or submitting anything other than `RESET`, leaves all data unchanged.

### `/factory_reset`

Removes every WASH-managed record belonging to the current server: guild configuration, suggestion databases and their configuration, suggestions (including embedded watch history), vote rounds, membership requests, scheduled watch parties, and scheduled reminder jobs. Backup archives, `.env` files, the bot token, application code, the virtual environment, and logs are never touched -- this command only ever writes through WASH's own JSON repositories.

Flow: `/factory_reset` -> WASH shows a count of everything that would be removed -> click **Factory Reset** -> type `RESET` exactly -> a full safety backup is made, then the reset runs. Afterward, `/setup` is required again (removing the guild's configuration is what makes WASH treat the server as never having been set up -- the same check `/setup` already used before this milestone).

### `/import`

Imports a backup produced by *another* WASH instance's own `/backup`. Unlike `/restore`, `/import` only ever accepts an uploaded `.zip` -- there is no "select an existing local backup" option, since the whole point is bringing in data WASH doesn't already have on disk.

Flow: upload the backup -> WASH validates it and shows the same kind of summary `/restore` shows -> choose **Merge**, **Replace**, or **Cancel** -> (Replace only) type `REPLACE` exactly -> a full safety backup is made, then the import runs.

Only "portable" data is ever imported: suggestion databases, their configuration, their suggestions, and vote rounds. This server's guild configuration -- its configured roles, channels, and guild ID -- is **never** changed by an import, in either mode. Membership requests, scheduled reminders, and scheduled watch parties are also never imported, since they reference the *source* server's Discord channels/messages/approval history and would be meaningless (or actively misleading) here.

#### Merge versus Replace

Never inferred -- you always choose explicitly:

- **Merge**: a database whose name already exists locally (case-insensitive match) has its suggestions merged in; a suggestion whose title already exists for that database is skipped and reported as a conflict, never overwritten. Every other incoming database is imported as new. Numeric IDs from the other instance are meaningless here (each WASH instance assigns them independently), so they're reassigned automatically whenever they'd otherwise collide with something already local.
- **Replace**: every portable record currently belonging to this guild is removed first, then the backup's portable data is imported fresh in its place. Other guilds' data (in a hypothetical multi-guild deployment) is untouched, and so is this guild's own Discord role/channel configuration.

#### Import results

After an import completes, WASH reports databases and suggestions imported vs. skipped, any title conflicts detected, how many identifiers were reassigned to avoid collisions, and which categories of data were intentionally excluded. WASH does not keep a persistent history of past imports -- each result is only shown once, in that response.

### Restart requirement

**A bot restart is recommended after `/restore`, `/database_restore`, `/database_reset`, `/factory_reset`, or `/import`.** Several services (suggestions, votes, membership requests) load their data once at startup and cache it in memory; changes written to disk by any of these commands won't be reflected in a running bot's behavior until it restarts. `GuildConfiguration` reads are not cached, so configuration changes (including a factory reset requiring `/setup` again) take effect immediately even without a restart.

### Recommended backup strategy

- Run `/backup` before any release, dependency upgrade, or manual data edit.
- Run `/database_backup` before experimenting with a specific database's suggestion rules or content.
- Keep at least one backup downloaded outside of WASH's own `data/backups/` directory (e.g. before a factory reset, since a factory reset's automatic safety backup still only lives in the same `data/` tree it's resetting).
- After using `/import`, review the reported conflicts and restart the bot before relying on the imported data.

### Troubleshooting

| Symptom | Cause | What happened to live data |
| --- | --- | --- |
| "This backup failed validation and cannot be restored" / "Import validation failed" | Corrupt ZIP, missing/unreadable manifest, unsafe path, or a checksum mismatch (tampered or truncated file). | Unchanged -- validation never writes anything. |
| "Unsupported backup type" | A full backup was offered to `/database_restore`, a single-database backup was offered to `/restore` or `/import`, or an incompatible format version was found. | Unchanged. |
| "That backup was created in a different Discord server" | A `/database_backup` archive's recorded guild ID doesn't match the server `/database_restore` was run in. | Unchanged. |
| "No existing suggestion database with that ID was found to merge into" | Merge was chosen but the destination database doesn't exist yet. | Unchanged -- use Replace instead if that's intended. |
| "N suggestion(s) were skipped as duplicates" (restore, reset, or import) | Merge detected a title already present in the destination database. | Only the non-conflicting suggestions were imported; nothing existing was overwritten. |
| "Confirmation text did not match ... exactly" | The typed `RESET`/`REPLACE` phrase didn't match, or didn't match case. | Unchanged -- nothing runs until the exact phrase is submitted. |
| "Safety backup failed, so the ... was aborted" (restore, reset, factory reset, or import) | WASH couldn't write the pre-action safety backup (e.g. disk full or permissions). | Unchanged -- the destructive action never began. |
| "Restore failed after the safety backup succeeded" | The safety backup was made, but copying the backup's files onto live data failed partway through. | The safety backup archive is intact and named in the error message; use `/restore` again with it if needed. |
| No suggestions appear after a successful restore/reset/import | A bot restart is required for the running process's in-memory cache to reflect the change (see "Restart requirement" above). | Data on disk is already correct; only the live bot's view of it is stale. |

## 10. Planned Version 1 Administration

The Version 1 plan includes:

- Guided setup and rerunnable configuration
- Existing, newly created, or deferred watch-history destinations
- Rotation and event-series administration
- Scheduling and Discord Event publishing
- Historical corrections and retroactive watch-history entry
- Configurable scheduled backup execution (the retention/interval settings already exist in `/config`; the scheduler does not yet act on them)
- Health and maintenance reporting

Until those features are implemented, `project_state.md` is authoritative about what administrators can use safely.
