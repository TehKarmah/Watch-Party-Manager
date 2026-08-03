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
- `/random watch` -- Pick one random eligible watch item from a collection, for when the group can't decide. Default selection is true random (no Favor New/Older Additions weighting); optional, combinable filters (Genre, IMDb Rating, MPAA Rating, Actor, and one eligible member's suggestions) narrow the random pool for this session only -- the setup/filter screen stays private, but once an item is found the result is posted publicly for the whole group, with Pick Again/Change Filters/Change Collection restricted to whoever ran the command. Pick Again keeps the active collection and filters and draws again -- immediate repeats are allowed. A discovery tool only: it never starts a vote or changes a suggestion's status.
- `/browse` -- WASH's interactive, paginated collection browser, available to every Watch Party member. Opens the shared filter menu first (Genre, IMDb Rating, MPAA Rating, Actor, Member -- the exact same menu `/random watch` and Custom Vote use), with a **View Results** button that applies the current filters and shows the paginated results -- results are never built or shown before View Results is clicked. From the results screen, Change Filters returns to that same filter menu (preserving every current filter and the selected collection), and Change Collection switches collections (rebuilding the eligible pool and clearing only the filters that no longer match anything there) and also returns to the filter menu, never straight to results; Previous/Next page through the matches. Each result shows title, year, IMDb rating, MPAA rating, genres, who suggested it, its status, and its reference number. If View Results finds nothing, a plain "no suggestions match" message offers Change Filters/Change Collection/Back instead of opening an empty results screen. WASH Crew additionally see **🎲 Random Pick** (a true-random draw from the current filtered results, reusing `/random watch`'s own selection logic), **🗳️ Start Vote** (opens Custom Vote's own "Customize This Vote" screen with the current collection and filters already carried over), and **📢 Post Publicly** (posts the current page publicly with its own Previous/Next, restricted to the WASH Crew member who posted it, without converting the private browsing session itself into a public one). Never starts a vote automatically, changes a suggestion's status, modifies a collection, or edits metadata on its own -- a browsing tool only.
- `/suggestion remove`
- `/suggestion edit`
- `/reject` / `/unreject` -- Command-line equivalents of a suggestion post's "I Won't Watch" button (see [Administration](05-Administration.md#rejecting-suggestions-i-wont-watch)). Configured per collection, with its own dedicated Setup Wizard step and `/config` section; it can also be disabled entirely.

### Voting

- `/vote start` -- Use Defaults, or Customize This Vote (Nominee Selection, Vote Visibility, candidate count, duration, and optional per-vote filters via Edit Filters -- Genre, IMDb Rating, MPAA Rating, Actor, and/or one member's suggestions; see [Administration](05-Administration.md#custom-vote-filters)).
- `/vote status` -- Shows the round's settings and, when active, its filters (Genre, IMDb Rating, MPAA Rating, Actor, Suggestion Source).
- `/vote edit`

Casting a vote itself happens through the interactive buttons on the voting post, not a slash command.

Wherever WASH asks for a duration (vote duration, reminder lead time, `/vote edit`'s Shorten/Extend Vote), it accepts a whole number immediately followed by a unit -- minutes, hours, or days -- for example `10m`, `1h`, or `7d`. Vote duration specifically means how long voting stays open, not the movie's own runtime.

### WASH Crew

- `/database add` -- Create a collection.
- `/database list` -- View collections.
- `/database health` -- Show a collection's eligibility and pool health (Eligible, In an Active Vote, Pending Crew Review, Vote Winners, Retired, Watched, Next Vote status, Low Pool status). Never changes any state.
- `/database manage` -- Guided workflow: pick a collection, then move, edit, back up, restore, refresh its IMDb metadata, recover missing IMDb links, reset, or remove it. This is the only way to move, back up, refresh, recover, reset, or remove a collection -- those actions have no separate top-level command (`/database restore` is the one exception; see the [Command Reference](10-Command-Reference.md)). Refresh IMDb Metadata re-fetches already-linked suggestions' IMDb details (title, plot, poster, runtime, genres, IMDb rating, MPAA/content rating, director, cast) -- useful for older suggestions that predate a field WASH now captures. Recover Missing IMDb Links instead finds suggestions with *no* IMDb link at all, searches OMDb by their stored title/year, and -- only once you approve a specific match -- saves it and immediately refreshes that suggestion's metadata; a suggestion already linked is never offered here. Both share the same scope model (This Collection or All Collections, active collections in the current server only) and always require confirmation first; neither ever changes a suggestion's status, history, or Discord references. See [Administration](05-Administration.md#imdb-metadata-refresh) and [IMDb Metadata Recovery](05-Administration.md#imdb-metadata-recovery).
- `/database restore` -- Restore one collection from a backup (Merge or Replace); stays a direct command since Discord can't collect a file upload through a button or menu.
- `/maintenance repair`
- `/maintenance backup` -- backs up WASH's own data only (suggestions, votes, collections, server configuration), never Discord itself.
- `/maintenance restore`
- `/maintenance reset`
- `/maintenance import`

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
- 🛠️ WASH Crew
- 🍿 Watch Party
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
