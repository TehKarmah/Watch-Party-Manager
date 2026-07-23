# Watch Party Manager

## Developer Guide

| Property | Value |
| --- | --- |
| Document | Developer Guide |
| File | `06-Developer-Guide.md` |
| Version | 1.0 Draft |
| Status | Active Draft |
| Last Updated | July 2026 |
| Authors | TehKarmah & ChatGPT |

## 1. Technology Stack

| Component | Current Choice |
| --- | --- |
| Language | Python 3.12 or later |
| Discord library | discord.py 2.4 or later |
| Configuration | Environment variables loaded through python-dotenv |
| Persistence | JSON repositories |
| Tests | Python unittest |
| Source control | Git and GitHub |
| Development environment | VS Code |
| Hosting | Self-hosted |

The Version 1 architecture may later introduce a database migration path. Current code and tests must treat JSON persistence as the implemented source of truth.

## 2. Project Structure

```text
Watch-Party-Manager/
├── docs/
├── src/watch_party_manager/
│   ├── domain/
│   ├── persistence/
│   ├── services/
│   ├── bot.py
│   ├── start_vote_view.py
│   └── voting_view.py
├── tests/
├── CHANGELOG.md
├── README.md
├── env.example
└── pyproject.toml
```

Responsibilities:

- `domain/` owns entities, validation, enums, and business invariants.
- `persistence/` owns serialization and storage details.
- `services/` coordinates application behavior without depending on Discord interactions.
- `bot.py` defines command wiring and Discord-facing helpers.
- View modules own Discord components and callback coordination.
- `tests/` mirrors implemented behavior with unit and integration-style tests.

## 3. Architectural Rules

Preserve these rules when adding features:

- Keep Discord objects out of domain models and repositories.
- Keep command callbacks thin and delegate business behavior to services or testable helpers.
- Keep repository access behind repository classes.
- Preserve historical records whenever practical.
- Keep all guild-owned data and operations guild-scoped.
- Fail closed for privileged actions when authorization is not configured.
- Reuse existing winner, standings, parsing, and formatting logic rather than duplicating it.
- Prefer small helpers with one clear responsibility.
- Keep user-facing dates and times in Discord timestamp format.

## 4. Local Setup

For a full first-time walkthrough (Discord bot creation, permissions, `.env` configuration, and the Setup Wizard), see the [Installation Guide](09-Installation-Guide.md). The quick version, for those already familiar with the project:

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item env.example .env
```

Populate `DISCORD_TOKEN` in `.env`. During command development, setting `DISCORD_GUILD_ID` provides faster synchronization than global commands.

Start WASH:

```powershell
.\.venv\Scripts\python.exe -m watch_party_manager.bot
```

### Resetting Local Data

`scripts/reset_dev_data.ps1` is a **developer-only** utility for repeated fresh-install testing. It permanently deletes local WASH runtime data under `data/` -- guild configuration, suggestions, suggestion databases, votes, rotations, scheduled jobs, setup wizard state, migration `.bak` artifacts, and everything inside `data/backups/` -- while preserving the `data/` and `data/backups/` directories themselves.

Run it from the repository root (or anywhere; it resolves paths relative to its own location):

```powershell
.\scripts\reset_dev_data.ps1
```

It prints a clear warning and requires typing `RESET` exactly before deleting anything; any other input cancels safely with nothing deleted. It never fails on missing files or folders, and prints a summary of what it removed.

This is never part of end-user installation -- it exists purely so a developer can repeatedly exercise `/setup` and first-run behavior without manually hunting down and deleting files. Local runtime data under `data/` is already excluded from Git (see `.gitignore`), so none of it is ever committed regardless of whether the script is used.

## 5. Testing

Run the complete suite before handing off or committing work:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Current baseline:

```text
Ran 2426 tests

OK
```

Testing requirements:

- Add focused tests for every new behavior.
- Add a regression test for every corrected defect when practical.
- Test domain validation independently from Discord wiring.
- Test repository serialization, malformed data behavior, and historical retention.
- Test guild scoping explicitly for guild-owned features.
- Test authorization success, failure, and unconfigured fail-closed behavior.
- Test restart restoration for persistent Discord interactions.
- Avoid duplicating large setup blocks. Reuse existing test helpers and factories.

A feature is not complete until the full suite passes, not only the new test module.

## 6. Discord Development

Commands are registered in `WatchPartyBot.setup_hook()`.

When adding or changing a command:

1. Put reusable behavior in a service or standalone helper.
2. Keep the interaction callback responsible for Discord input and output only.
3. Use ephemeral responses for private errors or administrative setup interactions when appropriate.
4. Confirm WASH Crew authorization for restricted commands.
5. Update `/help`, README command lists, administration documentation, and tests.
6. Run a focused Discord smoke test after automated tests pass.

Development guild synchronization is recommended during active work. Global synchronization can take longer to propagate.

## 7. Persistence

Current repositories store JSON data for suggestion databases, suggestions, and votes.

Repository work should:

- Validate loaded structures.
- Preserve stable identifiers.
- Avoid destructive history loss.
- Write deterministic, readable JSON where practical.
- Treat missing files as an empty initial state when the repository contract permits it.
- Surface malformed data with useful diagnostic context.
- Preserve backward compatibility or provide an explicit migration when formats change.

Never let Discord-specific values leak into repository APIs unless they are stored as plain identifiers.

## 8. Logging and Diagnostics

Use the configured module logger rather than `print()`.

Log meaningful operational events such as:

- Startup and command synchronization
- Administrative actions
- Suggestion and vote lifecycle changes
- Persistence or integrity failures
- Restoration of persistent Discord views
- Unexpected exceptions

Do not log bot tokens or other secrets. User-facing errors should be clear without exposing internal stack details.

## 9. Versioning and Documentation

The package version is defined in both `pyproject.toml` and `src/watch_party_manager/version.py`. Keep them synchronized.

Before a milestone handoff:

- Update `CHANGELOG.md`.
- Update `docs/project_state.md`.
- Update command documentation when behavior changes.
- Confirm the implementation checklist reflects completed foundations.
- Keep planned functionality clearly separated from implemented functionality.

## 10. Current Development Priorities

Voting lifecycle completion, watch-item journey/rotation tracking, setup/configuration workflows, statistics, and backup/restore/import are implemented. Remaining Version 1 priorities:

1. Wire automatic execution of the existing scheduled-backup interval/retention settings.
2. Build the richer Event Series/Discord Event scheduling foundation.
3. Add retroactive watch-history correction.

See `project_state.md` for the authoritative current milestone and known limitations.
