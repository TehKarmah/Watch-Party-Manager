# WASH Expanded Help

| Property | Value |
| --- | --- |
| Document | Expanded Help |
| File | `08-Expanded-Help.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | August 2026 |
| Authors | TehKarmah & ChatGPT |

This guide expands on the concise command reference shown by `/help` in Discord. It is the starting point for setup instructions, workflows, definitions, administration guidance, and troubleshooting.

## Commands

The current command list and short descriptions remain available through `/help` in Discord. Detailed command instructions will be added here as each workflow is refined.

### General

- `/help`
- `/about`
- `/stats`

### Watch Items

- `/add`
- `/list`
- `/random_watch` -- Pick one random eligible watch item from a collection, for when the group can't decide. Default selection is true random (no Favor New/Older Additions weighting); optional, combinable filters (one member's suggestions, one genre) narrow the random pool for this session only. Pick Again keeps the active collection and filters and draws again -- immediate repeats are allowed. A discovery tool only: it never starts a vote or changes a suggestion's status.
- `/remove`
- `/edit_suggestion`
- `/reject` / `/unreject` -- Command-line equivalents of a suggestion post's "I Won't Watch" button (see [Administration](05-Administration.md#rejecting-suggestions-i-wont-watch)). Configured per collection, with its own dedicated Setup Wizard step and `/config` section; it can also be disabled entirely.

### Voting

- `/vote start` -- Use Defaults, or Customize This Vote (Nominee Selection, Vote Visibility, candidate count, duration, and optional per-vote filters -- narrow nominees to one member's suggestions and/or one genre; see [Administration](05-Administration.md#custom-vote-filters)).
- `/vote status` -- Shows the round's settings and, when active, its filters (Suggestion Source, Genre).
- `/vote edit`

Casting a vote itself happens through the interactive buttons on the voting post, not a slash command.

Wherever WASH asks for a duration (vote duration, reminder lead time, `/vote edit`'s Shorten/Extend Vote), it accepts a whole number immediately followed by a unit -- minutes, hours, or days -- for example `10m`, `1h`, or `7d`.

### WASH Crew

- `/database add` -- Create a collection.
- `/database list` -- View collections.
- `/database health` -- Show a collection's eligibility and pool health (Eligible, In an Active Vote, Pending Crew Review, Vote Winners, Retired, Watched, Next Vote status, Low Pool status). Never changes any state.
- `/database manage` -- Guided workflow: pick a collection, then move, edit, back up, restore, reset, or remove it. This is the only way to move, back up, reset, or remove a collection -- those actions have no separate top-level command (`/database restore` is the one exception; see the [Command Reference](10-Command-Reference.md)).
- `/database restore` -- Restore one collection from a backup (Merge or Replace); stays a direct command since Discord can't collect a file upload through a button or menu.
- `/repair_suggestions`
- `/backup`
- `/restore`
- `/factory_reset`
- `/import`

## Help Topics

- [Getting started](../README.md)
- [Administration](05-Administration.md)
- [Complete documentation](00-Table-of-Contents.md)
- [Terminology & concepts](98-Glossary.md)
- [Current project state](project_state.md)

## Terminology & Concepts

The complete definitions are maintained in [Terminology & Concepts](98-Glossary.md). Important terms include:

- Watch Item
- Collections (Suggestion Database)
- WASH Crew
- Blind Vote
- Visible Vote
- Journey
- Eligible Pool
- Voting Round
- "I Won't Watch"

## Planned Expansion

Adding, listing, editing, and removing suggestions; suggestion database behavior; and backup/restore/reset/import workflows are documented in [Administration](05-Administration.md). This page will still be refined to include:

- Getting started and initial server setup
- Starting and participating in votes
- WASH Crew permissions and administration
- Frequently asked questions
- Troubleshooting
