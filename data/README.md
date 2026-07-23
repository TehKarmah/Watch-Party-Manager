# data/

This directory holds WASH's local runtime state: per-guild configuration, suggestion databases, suggestions, votes, rotations, scheduled jobs, setup wizard progress, and backup archives (`data/backups/`).

Everything WASH writes here is local runtime data, not source code -- it is intentionally excluded from Git (see `.gitignore`) and is never committed. Only this file and `.gitkeep` placeholders are tracked, so the directory structure exists in a fresh clone even though its contents don't.

The directory (and `data/backups/`) is created automatically on first run if missing -- nothing needs to be set up by hand.

For developers who want to reset local data and re-test a fresh installation, see `scripts/reset_dev_data.ps1` (documented in the [Developer Guide](../docs/06-Developer-Guide.md)).
