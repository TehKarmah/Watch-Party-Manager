# WASH Expanded Help

| Property | Value |
| --- | --- |
| Document | Expanded Help |
| File | `08-Expanded-Help.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | July 2026 |
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
- `/remove`
- `/edit_suggestion`

### Voting

- `/vote start`
- `/vote status`
- `/vote edit`

Casting a vote itself happens through the interactive buttons on the voting post, not a slash command. When a vote closes, its results post gains a **Schedule Watch Party** button (or **Choose Winner to Schedule**, if there was a tie) -- this is the normal way WASH Crew schedules the watch party for the winning title, and it creates a real Discord Scheduled Event, not just an internal WASH record. See [Administration](05-Administration.md) for the full Watch Party lifecycle.

### WASH Crew

- `/database add` -- Create a collection.
- `/database list` -- View collections.
- `/database manage` -- Move, edit, back up, restore, reset, or remove collections (additional shortcuts -- `/database move`, `/database backup`, `/database restore`, `/database reset`, `/database remove` -- are documented in the [Command Reference](10-Command-Reference.md)).
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
- Collection (Suggestion Database)
- WASH Crew
- Blind Vote
- Visible Vote
- Journey
- Rotation
- Voting Round
- Watch Party

## Planned Expansion

Adding, listing, editing, and removing suggestions; suggestion database behavior; and backup/restore/reset/import workflows are documented in [Administration](05-Administration.md). This page will still be refined to include:

- Getting started and initial server setup
- Starting and participating in votes
- WASH Crew permissions and administration
- Frequently asked questions
- Troubleshooting
