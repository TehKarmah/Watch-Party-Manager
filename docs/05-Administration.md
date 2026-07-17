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
3. Back up the data directory manually.
4. update the checked-out repository.
5. Activate the virtual environment and install updated dependencies if needed.
6. Start WASH and review startup logs.
7. Run `/diagnostics` from an authorized account.
8. Smoke-test any commands changed in the release.

Automated backup, restore, import, export, and migrations are planned but not implemented.

## 9. Planned Version 1 Administration

The Version 1 plan includes:

- Guided setup and rerunnable configuration
- Existing, newly created, or deferred watch-history destinations
- Rotation and event-series administration
- Scheduling and Discord Event publishing
- Historical corrections and retroactive watch-history entry
- Backup and restore
- Import and export
- Configurable scheduled backups and retention
- Health and maintenance reporting

Until those features are implemented, `project_state.md` is authoritative about what administrators can use safely.
