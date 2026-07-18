# Watch Party Manager

Watch Party Manager is a configurable, self-hosted Discord bot for managing recurring watch parties. The Discord assistant is named **WASH**, short for **Watch Party Administration & Scheduling Helper**.

WASH currently supports suggestion databases, suggestion management, nominee selection, interactive voting, statistics, diagnostics, and persistent JSON storage. Development is active and the project is not yet ready for general release.

## Current Status

- **Version:** 0.1.0
- **Current milestone:** Voting lifecycle completion
- **Automated tests:** 569 passing
- **Python:** 3.12 or later
- **Discord library:** discord.py 2.4 or later
- **Persistence:** JSON repositories

Implemented foundations include:

- Watch Item and Watch Item Journey domain models
- Guild-scoped suggestion databases
- Adding, listing, and removing suggestions
- Intelligent nominee selection
- Blind or visible voting rounds
- Interactive Discord voting controls
- Persistent voting restoration after restart
- Vote standings, deterministic winner calculation, and tie support
- WASH Crew role permissions that fail closed when unconfigured
- Statistics, diagnostics, data-integrity checks, and structured logging

Automatic vote completion, winner announcements, and watch-history updates are the next active development work.

## Discord Commands

### Community commands

- `/version`
- `/help`
- `/add`
- `/list`
- `/remove`
- `/vote`
- `/vote_status`
- `/stats`

### WASH Crew commands

- `/start_vote`
- `/database_add`
- `/database_list`
- `/database_remove`
- `/diagnostics`

WASH Crew commands require the role configured through `WASH_CREW_ROLE_ID`. When that setting is absent, restricted commands fail closed.

## Local Setup

1. Install Python 3.12 or later.
2. Create and activate a virtual environment.
3. Install the project in editable mode.
4. Copy `env.example` to `.env` and provide a Discord bot token.
5. Start WASH.

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item env.example .env
python -m watch_party_manager.bot
```

Run the full test suite:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Configuration

The current environment settings are documented in `env.example`:

- `DISCORD_TOKEN`
- `DISCORD_GUILD_ID` for faster development-guild command synchronization
- `WASH_CREW_ROLE_ID`
- `DEFAULT_VOTE_NOMINEE_COUNT`

## Documentation

- [Documentation Table of Contents](docs/00-Table-of-Contents.md)
- [Product Vision](docs/01-Product-Vision.md)
- [Architecture](docs/02-Architecture.md)
- [Functional Specification](docs/03-Functional-Specification.md)
- [Data Model](docs/04-Data-Model.md)
- [Administration](docs/05-Administration.md)
- [Developer Guide](docs/06-Developer-Guide.md)
- [Implementation Checklist](docs/07-Implementation-Checklist.md)
- [Current Project State](docs/project_state.md)

## License

See [LICENSE](LICENSE). The current license permits personal and non-commercial use under its stated terms.
