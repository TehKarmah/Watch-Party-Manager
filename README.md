# Watch Party Manager

Watch Party Manager is a configurable, self-hosted Discord bot for managing recurring watch parties. The Discord assistant is named **WASH**, short for **Watch Party Administration & Scheduling Helper**.

WASH manages suggestion databases and suggestions (with IMDb link normalization and duplicate detection), intelligent rotation-aware nominee selection, blind or visible interactive voting, scheduled watch parties, statistics and reporting, a guided in-Discord setup wizard, and backup/restore/import tooling -- all backed by persistent JSON storage.

## Current Status

- **Version:** 0.1.0 (final release preparation)
- **Automated tests:** 2426 passing
- **Python:** 3.12 or later
- **Discord library:** discord.py 2.4 or later
- **Persistence:** JSON repositories

Implemented capabilities include:

- Watch Item and Watch Item Journey domain models, with a guided in-Discord Setup Wizard (`/setup`) and an always-editable configuration menu (`/config`)
- Guild-scoped suggestion databases with adding, listing, editing, and removing suggestions
- IMDb link normalization and duplicate detection (definite and possible-duplicate warnings) on add and edit
- Configurable candidate-selection strategies (Rotation Pool, Soft Rotation, Infinite Pool) with rotation lifecycle tracking
- Blind or visible voting rounds with interactive Discord controls, persistent restoration after restart, deterministic standings, and tie support
- Automatic vote completion, winner announcements, and Watch Item Journey updates
- Watch Party membership workflows (self-service, manual, approval-required, or Discord-managed) and scheduled watch parties with reminders
- Server, member, suggestion, rotation, and database statistics (`/stats`), with privacy-scoped ephemeral/public output
- Backup, restore, per-database backup/restore, factory reset, and cross-instance import, all with safety backups before destructive actions
- WASH Crew and Watch Party member role permissions that fail closed when unconfigured
- Diagnostics, data-integrity checks, and structured logging

See [Current Project State](docs/project_state.md) for the authoritative, detailed implementation status.

## Quick Start

New to WASH? Follow the complete [Installation Guide](docs/09-Installation-Guide.md) for step-by-step instructions, including Discord bot creation, permissions, and `.env` configuration.

Already familiar with Discord bot hosting? PowerShell:

```powershell
git clone https://github.com/TehKarmah/Watch-Party-Manager.git
cd Watch-Party-Manager
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item env.example .env
# Edit .env and set DISCORD_TOKEN, then:
python -m watch_party_manager.bot
```

Then run `/setup` in your server as a server administrator to finish configuration.

Run the full test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Discord Commands

WASH's in-Discord `/help` command always reflects the exact command set available to the person running it. The summary below groups commands by who can use them; see [Administration](docs/05-Administration.md) for full behavior.

### Everyone

- `/help`, `/about`, `/join_watch_party`

### Watch Party members (WASH Crew inherit these too)

- `/add`, `/list`, `/stats`

### WASH Crew

- Suggestions: `/remove`, `/edit_suggestion`, `/repair_suggestions`
- Voting: `/start_vote`, `/vote_status`, `/edit_vote`
- Suggestion databases: `/database_add`, `/database_list`, `/database_remove`, `/database_backup`, `/database_restore`, `/database_reset`
- Watch parties: `/schedule_watch_party`, `/reschedule_watch_party`, `/cancel_watch_party`, `/watch_party_status`
- Membership: `/watch_party`
- Configuration: `/setup`, `/config`
- Maintenance: `/backup`, `/restore`, `/factory_reset`, `/import`

WASH Crew commands require the role configured through `WASH_CREW_ROLE_ID` (or the Setup Wizard's WASH Crew step). Watch Party member commands require `WATCH_PARTY_MEMBER_ROLE_ID` or the wizard's Watch Party role step. Both fail closed when unconfigured -- nobody can use them until a role is set.

## Configuration

The current environment settings are documented in `env.example`:

- `DISCORD_TOKEN` (required)
- `DISCORD_GUILD_ID` for faster development-guild command synchronization (optional)
- `WASH_CREW_ROLE_ID` (optional -- can also be set via `/setup`)
- `WATCH_PARTY_MEMBER_ROLE_ID` (optional -- can also be set via `/setup`)
- `DEFAULT_VOTE_NOMINEE_COUNT` (optional)
- `OMDB_API_KEY` for resolving pasted IMDb links (optional)

Everything else -- suggestion databases, voting defaults, reminders, backup schedule, and more -- is configured per-server through `/setup` and `/config` once WASH is running. See the [Installation Guide](docs/09-Installation-Guide.md) for the full walkthrough.

## Documentation

- [Documentation Table of Contents](docs/00-Table-of-Contents.md)
- [Installation Guide](docs/09-Installation-Guide.md)
- [Product Vision](docs/01-Product-Vision.md)
- [Architecture](docs/02-Architecture.md)
- [Functional Specification](docs/03-Functional-Specification.md)
- [Data Model](docs/04-Data-Model.md)
- [Administration](docs/05-Administration.md)
- [Developer Guide](docs/06-Developer-Guide.md)
- [Implementation Checklist](docs/07-Implementation-Checklist.md)
- [Commands Reference](docs/10-Command-Reference.md)
- [Expanded Help](docs/08-Expanded-Help.md)
- [Glossary](docs/98-Glossary.md)
- [Current Project State](docs/project_state.md)
- [Changelog](CHANGELOG.md)

## License

See [LICENSE](LICENSE). The current license permits personal and non-commercial use under its stated terms.
