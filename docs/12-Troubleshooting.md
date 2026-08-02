# Watch Party Manager

## Troubleshooting Guide

| Property     | Value                     |
| ------------ | ------------------------- |
| Document     | Troubleshooting Guide     |
| File         | `12-Troubleshooting.md`   |
| Version      | 1.0                       |
| Status       | Active                    |
| Last Updated | August 2026               |
| Authors      | TehKarmah & ChatGPT       |

---

> [!NOTE]
> This is the first place to look when something in WASH isn't working as expected. It groups symptoms by area and points at the exact section of another document that explains the full behavior, rather than repeating it here. If your problem isn't listed, see [General](#general) for how to read WASH's logs and what to include in a bug report.

---

## Table of Contents

1. [Installation](#installation)
2. [Setup Wizard](#setup-wizard)
3. [Configuration](#configuration)
4. [Voting](#voting)
5. [Crew Review](#crew-review)
6. [Backups](#backups)
7. [Discord](#discord)
8. [General](#general)

---

## Installation

The [Installation Guide](09-Installation-Guide.md)'s own [Section 14, Troubleshooting](09-Installation-Guide.md#14-troubleshooting) is the authoritative table for every installation symptom -- the three most common are called out here.

### Bot won't join server

WASH was invited without the `bot` OAuth2 scope, or the invite link was built from an incomplete permission selection. Re-invite WASH from the Discord Developer Portal with both **bot** and **applications.commands** selected under Guild Install (Installation Guide, [Section 6](09-Installation-Guide.md#6-discord-developer-portal-setup) and [Section 9](09-Installation-Guide.md#9-invite-wash-to-your-server)), and confirm WASH now appears in the server's member list.

### Missing permissions

WASH can't post in a specific channel, create a channel, or create a thread. This is almost always a channel-level permission overwrite, not a server-wide one -- server-wide invite permissions don't override a specific channel's own overwrites. Check that channel's permission overwrites for WASH's role (View Channel, Send Messages, Embed Links, and -- for the Home Channel specifically -- Manage Channels/Create Public Threads). See also [Setup Wizard: Cannot create channels/threads](#cannot-create-channels) below.

### Missing scopes

Slash commands respond, but WASH never appears as an actual member (breaks `/setup`'s first-run owner check and anything else that depends on WASH being a member). This means only `applications.commands` was granted, not `bot`. Re-invite with both scopes selected -- see [Bot won't join server](#bot-wont-join-server) above.

---

## Setup Wizard

### Cannot create channels

The Setup Wizard's **Create New Channel** option (Home Channel step) fails with a permission message. WASH needs the **Manage Channels** permission at the server or category level. Grant it and try again, or choose **Use Existing Channel** instead for that one step. If WASH also assigns server roles, its own role must be positioned above those roles in the server's role hierarchy (see [Role hierarchy](#role-hierarchy) below) -- this can produce a similar-looking permission failure even when Manage Channels is granted.

### Cannot create threads

Every collection's suggestion thread (and, by default, the Watched Item Archive thread) is created as a sibling thread under WASH's configured Home Channel. If **Create New Thread** fails, WASH is missing **Create Public Threads** (or **Send Messages in Threads**) on that Home Channel specifically -- a channel-level permission, independent of Manage Channels. Grant it on the Home Channel, or choose **Use Existing Thread** instead. A newly created private channel (e.g. the Admin Channel) is visible only to WASH Crew until WASH itself is granted View Channel/Send Messages on it -- reopen the step (or run `/setup` again) once that's done to see it in the destination list.

### Cannot access private channels

A private channel or thread doesn't appear in a destination picker. WASH can only offer channels/threads it can actually see -- grant it **View Channel** (and **Send Messages**) on the one you want, then reopen the current step or re-run `/setup`/`/config` to refresh the list.

### Resume interrupted setup

`/setup` is resumable by design: every step saves its answer immediately, so closing Discord, a bot restart, or simply running `/setup` again later all resume exactly where you left off, offering **Continue**, **Review Progress**, or **Restart** rather than losing any previously entered value. **Save & Finish Later** on any step does the same thing explicitly. If setup genuinely needs to start over, choose **Restart** -- this discards the in-progress draft only, never anything already saved to the server's live configuration from a *previous, completed* setup. See [Administration, Section 1](05-Administration.md#1-current-administrative-model) and the Installation Guide's [Section 11](09-Installation-Guide.md#11-run-the-setup-wizard).

---

## Configuration

### Missing configuration

A command replies "WASH Crew permissions have not been configured," "Run `/setup` before using `/config`," or similar. `/config` is only usable once `/setup` has been completed at least once -- run `/setup` first. If a specific role or channel shows as "Invalid" on `/config`'s main menu, the configured role/channel was deleted or WASH lost access to it; reconfigure that one section from `/config`'s numbered menu (each section explains its own screen; the menu itself is deliberately description-free -- see [Command Reference](10-Command-Reference.md)).

### Collection problems

- **A collection doesn't appear where expected**: collections default to threads created under the configured Home Channel -- check `/database list` for its Active/Inactive status and current destination.
- **Can't tell which collection a command will use**: most commands resolve the collection automatically from the thread they're run in; use the **Switch Collection** button (offered whenever more than one collection exists) rather than guessing.
- **A setting doesn't seem to apply**: several settings are per-collection (Nominee Selection, "I Won't Watch" Enable/threshold, Suggestion Destination) while others are guild-wide (Vote Visibility, candidate count, vote duration) -- see [Administration, Section 4](05-Administration.md#4-starting-a-vote) and the "I Won't Watch" section below for exactly which is which.
- **Need to change a collection's destination, back it up, refresh its IMDb metadata, reset it, or remove it**: all of these live under `/database manage` -- pick the collection, then choose the action. See [Command Reference](10-Command-Reference.md) for the full `/database` command list.

### IMDb Metadata Refresh: suggestion(s) reported as Skipped

A suggestion is Skipped, never attempted, whenever it has no usable persisted IMDb link (added by plain title with no IMDb URL, or an unresolvable one). This is deliberate -- the refresh never guesses a title's IMDb match, so a Skipped suggestion needs a usable IMDb link added first (e.g. via `/edit_suggestion`, or by re-adding it with `/add` and a correct IMDb URL/link) before a future refresh can pick it up.

### IMDb Metadata Refresh: some suggestions reported as Failed

A Failed suggestion had a usable IMDb link but the lookup, parsing, or save step didn't succeed -- check the bot's logs around the time of the refresh for the specific guild, collection, suggestion, and IMDb ID involved (never a secret or API key). Common causes: OMDb temporarily unreachable or rate-limited, an IMDb ID that no longer resolves, or (rare) a refreshed title colliding with another suggestion's title already in the same collection. Simply running Refresh IMDb Metadata again is safe -- it's idempotent, and only re-attempts what still needs it; anything already successfully refreshed is reported as Unchanged, not redone or duplicated.

### IMDb Metadata Refresh: the bot restarted partway through

Nothing is lost. Every suggestion that had already been successfully refreshed and saved before the restart stays saved -- there's no separate "in-progress" state that a restart could corrupt. Run Refresh IMDb Metadata again (This Collection or All Collections, whichever was interrupted) to pick up any suggestions that weren't reached yet; anything already refreshed reports as Unchanged and is never reprocessed or duplicated.

### IMDb Metadata Refresh: it's taking a while / the bot's log still shows an occasional 429

A refresh deliberately paces suggestion-post edits landing in the same Discord channel to avoid tripping Discord's message-edit rate limit, so a collection with many refreshed suggestions in one channel will visibly take longer than the lookups alone would. This is expected and does not affect the final results -- metadata is saved as soon as each lookup succeeds, independent of whether or when its post gets resynced. An occasional 429 in the log with a short automatic retry is still normal (Discord's own client-side retry handling, not a failure); it should simply be rarer than before. A post-sync failure (not the same as a metadata Failed) is reported separately and never rolls back an already-saved metadata update -- see "some suggestions reported as Failed" above for the distinct metadata-lookup failure case.

---

## Voting

### Vote won't start

`/vote start` refuses before creating anything if the collection can't support the requested vote -- no round, persistent view, or announcement is ever partially created. Common causes:

- No collection resolved (ambiguous thread context) -- run it from inside a collection's own thread, or choose one from the picker.
- WASH Crew role required -- `/vote start` is WASH Crew only.
- A voting round is already open for that collection -- close or cancel it first (`/vote status`, `/vote edit`).

### Not enough eligible suggestions

`/vote start` reports the collection's actual eligible count against the requested candidate count and refuses to start, e.g. *"This vote requires 3 candidates, but only 2 are currently available. Add more suggestions, reduce the candidate count, or review your Vote Winners and Retired items."* Check `/database health` for the exact breakdown (Eligible for Voting, In an Active Vote, Pending Crew Review, and so on) -- items in every other status don't count toward this total. See [Administration: Rotation & Collection Health](05-Administration.md#rotation--collection-health) and [Eligible Pool Warning](05-Administration.md#eligible-pool-warning) (a proactive low-pool notification, configurable per server).

### Nominee filters produce too few candidates

Customize This Vote's optional Genre, IMDb Rating, MPAA Rating, Actor, and Suggestion Source (one member) filters narrow the eligible pool *before* Nominee Selection runs, for that one round only. If the filtered pool can't support the requested candidate count, the round is not created and the error names the active filter(s), the remaining count, and how to resolve it, e.g.:

> KC has 2 eligible suggestions, but this vote requires 3 nominees. Reduce the candidate count or choose another member.
>
> The Horror filter leaves 2 eligible suggestions, but this vote requires 3 nominees. Reduce the candidate count or choose another genre.

Reduce the candidate count, change or combine fewer filters, or reset the relevant filter(s) back to Any. See [Administration: Custom Vote Filters](05-Administration.md#custom-vote-filters) for the full behavior, including how combined filters intersect.

### IMDb Rating, MPAA Rating, or Actor filter has no options / never matches

All three of these filters (and Genre) only ever read metadata already stored on a suggestion at the time it was added -- they never make a live IMDb lookup when the filter menu opens or a vote starts. A suggestion added before its collection had reliable metadata (or added by direct title with no matching IMDb result) may simply have no IMDb rating, no content rating, and/or no cast recorded; it will never match an active filter for that field, but remains fully eligible whenever that filter is set to Any. MPAA Rating's dropdown and Actor's search results are both built only from suggestions that *do* have that metadata in the current eligible pool, so a field that's empty for every suggestion in a collection will show no MPAA Rating options (beyond Any) or no Actor search results at all. There is currently no bulk "refresh metadata for existing suggestions" tool -- re-adding an item via `/add` with the same or a corrected IMDb URL re-resolves and re-stores its metadata (including cast), which is the current workaround for a specific item; a project-wide backfill for every pre-existing suggestion is a known gap, not something this release attempts automatically (see the project's CHANGELOG for the specific milestone that introduced cast metadata).

---

## Crew Review

### "I Won't Watch" disabled

If a suggestion's public post has no **I Won't Watch** button at all, "I Won't Watch" is disabled for that collection -- this is an intentional, per-collection setting (its own dedicated Setup Wizard step and `/config` section), not a bug. `/reject` also refuses with a clear "disabled for this collection" message while it's off. Check or change it via `/config` -> **"I Won't Watch" Settings** (choose the collection first if the server has more than one). Disabling never discards previously recorded rejections, and re-enabling never retroactively re-evaluates an existing below-threshold count against the threshold -- only a genuinely new rejection can ever cross it. See [Administration: Rejecting suggestions ("I Won't Watch")](05-Administration.md#rejecting-suggestions-i-wont-watch).

### Pending Crew Review

A suggestion stuck at ⚠️ **Pending Crew Review** is expected once its collection's configured "I Won't Watch" threshold is reached -- it's immediately excluded from nominee eligibility and its button is disabled until WASH Crew resolves it from the notification posted in the configured Admin Channel. Only **Retire**, **Keep Active**, or **Reset Rejections** (all WASH Crew only) clear this status; nothing resolves it automatically. If the Admin Channel notification itself seems to be missing, confirm an Admin Channel is actually configured (`/config`) -- without one, WASH Crew is never notified, though the suggestion still correctly moves to Pending Crew Review. See [Administration: Rejecting suggestions ("I Won't Watch")](05-Administration.md#rejecting-suggestions-i-wont-watch).

---

## Backups

Full command-by-command behavior, safety-backup guarantees, and a dedicated symptom table already live in [Administration, Section 9: Backup & Recovery](05-Administration.md#9-backup--recovery) -- see its own [Troubleshooting subsection](05-Administration.md#troubleshooting) for the complete list (corrupt/invalid backups, mismatched server IDs, confirmation-text mismatches, safety-backup failures, and more). Two points worth calling out specifically:

### Backup

Run `/backup` (full server) or `/database backup` (one collection) before any release, dependency upgrade, manual data edit, or experiment with a collection's rules -- see [Administration: Recommended backup strategy](05-Administration.md#recommended-backup-strategy). `/database backup` is reachable directly or through `/database manage`'s Backup Collection action; both run the exact same logic.

### Restore

**A bot restart is required after `/restore`, `/database restore`, `/database reset`, `/factory_reset`, or `/import`** for the change to actually take effect -- several repositories cache their data in memory at startup, so "nothing happened" after a successful restore almost always just means WASH hasn't been restarted yet. See [Administration: Restart requirement](05-Administration.md#restart-requirement). `/database restore` remains its own top-level command (rather than living inside `/database manage`) because it requires uploading a file, and Discord has no way to attach a file in response to a button or menu selection -- `/database manage`'s Restore Collection action explains this and points you at `/database restore` directly.

---

## Discord

### Slash commands missing

Global command sync can take up to an hour after WASH first joins a server. Set `DISCORD_GUILD_ID` in `.env` for instant sync during development/testing, or simply wait. If commands never appear at all, re-invite WASH with the `applications.commands` scope (see [Missing scopes](#missing-scopes) above). See [Installation Guide, Section 14](09-Installation-Guide.md#14-troubleshooting).

### Permissions

Most "WASH can't do X" symptoms are a missing permission on a specific channel, not a server-wide problem -- see [Missing permissions](#missing-permissions) above. WASH Crew-only and Watch Party member-only commands fail closed with an explicit message naming the required role when it isn't configured or the invoking member doesn't hold it; this is expected behavior, not an error.

### Role hierarchy

If WASH is expected to manage roles (e.g. assigning the Watch Party role automatically) but role assignment silently fails, WASH's own bot role must be positioned **above** any role it needs to assign or remove, in the server's Role settings -- Discord never allows a bot to manage a role positioned above its own highest role, regardless of the permissions granted.

---

## General

### Reading logs

WASH logs to the console (standard output) it was started from -- there is currently no separate log file. If you're running WASH via a process manager or container, its own log viewer (e.g. `docker logs`, `journalctl`, or your host's console output) is where these lines appear. Look for:

- A startup block confirming Discord connection and command sync.
- `WARNING`/`ERROR`-level lines around the time your symptom occurred -- most failures WASH can anticipate (a deleted channel, a validation failure, a Discord API rejection) are logged with enough context (guild ID, suggestion ID, round ID) to correlate with what a WASH Crew member was doing.
- A full Python traceback for anything genuinely unexpected -- include this verbatim in a bug report (see below).

### Common error messages

| Message | Meaning |
| --- | --- |
| "WASH Crew permissions have not been configured. Set WASH_CREW_ROLE_ID before using this command." | No WASH Crew role has ever been configured (neither via `/setup` nor the `WASH_CREW_ROLE_ID` environment variable) -- every WASH Crew-only command fails closed until one is. |
| "You need the WASH Crew role to \[do X\]." | A WASH Crew role is configured, but the invoking member doesn't hold it. |
| "You need the Watch Party member role to \[do X\]." | Similarly, for the Watch Party role -- see [Missing configuration](#missing-configuration) above. |
| "This command can only be used in a server." | The command was run in a DM, where WASH has no guild context to resolve. |
| "That collection no longer exists." / "No collections exist in this server yet." | The referenced collection was removed/deactivated, or none has been created yet -- `/database add`. |
| "Run `/setup` before using `/config`." | First-time setup was never completed for this server. |

### What information to include in a bug report

- The exact command run (including any options/choices selected) and the exact message WASH replied with, copied verbatim.
- WASH's version/build and health snapshot from `/about` (WASH Crew see additional Health/Configuration/Runtime detail there -- see [Command Reference](10-Command-Reference.md)).
- Whether the problem is reproducible, and the minimal steps to reproduce it.
- Relevant log lines from around the time it happened (see [Reading logs](#reading-logs) above), especially any traceback.
- Whether anything unusual happened recently (a restore/reset/import, a permission change, a Discord outage).
