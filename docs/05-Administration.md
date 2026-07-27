# Watch Party Manager

## Administration

| Property | Value |
| --- | --- |
| Document | Administration |
| File | `05-Administration.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | July 2026 |
| Authors | TehKarmah & ChatGPT |

> [!NOTE]
> This document distinguishes administration available in the current 1.0.0 build from the broader Version 1 administration plan. For a complete first-time installation walkthrough, see the [Installation Guide](09-Installation-Guide.md).

## 1. Current Administrative Model

WASH uses two configured Discord roles: **WASH Crew** for restricted administrative operations, and **Watch Party member** for participant commands (WASH Crew automatically inherit member permissions). Both can be set via `WASH_CREW_ROLE_ID`/`WATCH_PARTY_MEMBER_ROLE_ID` in `.env`, or interactively through the guided `/setup` wizard.

Restricted commands fail closed. When a required role isn't configured, no user can run the commands that depend on it -- including server administrators, unless they also happen to hold the configured role.

For the exact, current, permission-scoped command list, run `/help` in Discord -- it always reflects exactly what the requesting user can do. See the [README](../README.md) for a grouped summary, or [Expanded Help](08-Expanded-Help.md) for the same reference `/help` links to.

The guided setup wizard (`/setup`) and the always-available configuration menu (`/config`) are both implemented -- see Section 3's "Adding suggestions" onward for the workflows they enable, and the [Installation Guide](09-Installation-Guide.md) for a full first-run walkthrough.

## 2. Environment Configuration

Copy `env.example` to `.env` and configure the values needed for the installation. See the [Installation Guide](09-Installation-Guide.md) for how to obtain a Discord bot token and an OMDb API key.

| Setting | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Authenticates the Discord bot. |
| `DISCORD_GUILD_ID` | No | Synchronizes commands to one development guild for faster testing. |
| `WASH_CREW_ROLE_ID` | No -- can also be set via `/setup` | Authorizes restricted administration commands. |
| `WATCH_PARTY_MEMBER_ROLE_ID` | No -- can also be set via `/setup` | Authorizes participant commands (`/add`, `/list`, `/stats`, etc.). |
| `DEFAULT_VOTE_NOMINEE_COUNT` | No | Sets the default nominee count from 2 through 10. The default is 3. |
| `OMDB_API_KEY` | No | Enables resolving pasted IMDb links into title/runtime/genre/poster metadata for `/add`. Plain-title suggestions work without it. |

Do not commit the populated `.env` file.

## 3. Suggestion Database Administration

Suggestion databases organize Watch Items within a Discord server and channel context.

Collections live in threads only. WASH's configured Watch Party Home Channel exists to organize collection threads and host discussion/announcements/navigation -- it can never itself become a collection's suggestion destination, whether created fresh, moved to, or edited into that role via `/config`.

### Create a database

Run `/database add`. It first asks what type of collection to create: every standard type (Movies, TV Shows, Anime, Holiday, Documentaries, Horror) this server doesn't already have a matching collection for, plus Special Collection and Custom, which are always offered. Special Collection and Custom collect a name through a short modal; the standard types use their own descriptive default name (e.g. Movies -> "Movie Suggestions") with no typing required. It then asks where suggestions should post -- **Create New Thread (Recommended)** (created as a sibling under WASH's configured Home Channel, with the suggested name editable before creation; if no Home Channel is configured or it's no longer available, the thread is created under the current channel instead when that's a usable text channel, otherwise the button is disabled with a clear explanation), **Use Current Thread** (only enabled when `/database add` was actually run inside a thread), or **Use Existing Thread** -- and creates the collection there immediately. Database names must still be valid under the service's normalization and duplicate-name rules, and the chosen destination must not already be routed to another collection.

### List databases

Use `/database list` to review databases available to the current server.

### Move a database's suggestion destination

Run `/database move`, choose the collection from the picker that appears, then choose its new destination using the exact same Create New Thread/Use Current Thread/Use Existing Thread choice `/database add` offers. Only the collection's suggestion destination changes -- its database ID, suggestions, statuses, vote history, rotation history, statistics, and every other setting are untouched. Existing Discord suggestion posts are never moved or edited; only suggestions added after the move post to the new destination. The chosen destination must not already be routed to another collection, must not be WASH's configured Home Channel, and a failure after a new thread is created (e.g. a duplicate destination) automatically deletes that thread rather than leaving it orphaned.

**Context Resolution Audit (bug fix):** every command that resolves "which collection applies here" (`/add`, `/list`, `/vote start`, `/stats`, and `/database add`'s own duplicate-channel check) shares one implementation, `resolve_database_for_channel`, itself built on the same single-channel resolver (`resolve_collection_channel_id`) `/database list` and `/config` already used for display. Previously, a moved collection's *original* channel kept resolving alongside its new one -- running a command from the old location could still silently reach the collection that had just been moved away from it. A collection's original channel now stops resolving the moment it's moved; only its current destination does, and that destination is also immediately free for a different collection to move into. This takes effect immediately (no restart needed) and survives one, since nothing about it is cached in memory -- resolution is always computed fresh from the same persisted records `/database list`/`/config` already read.

### Guided collection management

Run `/database manage` for a single guided entry point instead of remembering which direct subcommand to use: choose a collection from the same picker `/database move`/`backup`/`reset`/`remove` use, then choose an action from a menu -- **Move Collection**, **Edit Collection**, **Backup Collection**, **Restore Collection**, **Reset Collection**, **Remove Collection**, or **Cancel**. Move/Backup/Reset/Remove run the exact same logic as their direct subcommand (nothing about their behavior differs based on which path reached them). Edit Collection opens the same per-collection settings menu `/config`'s Manage Collections section already uses -- Suggestion Destination, Watched Movie Destination, Candidate Selection -- with its Back button returning to this menu instead of `/config`'s own collection list. Restore Collection can't be driven from a button click at all (Discord doesn't allow attaching a file upload in response to one), so it points at running `/database restore` directly, the same way the Setup Wizard's "Import Existing Database" option points at `/import`. The direct subcommands (`/database move`, `/database backup`, `/database restore`, `/database reset`, `/database remove`) remain available as shortcuts and are unchanged by `/database manage`'s existence.

### Remove a database

Run `/database remove`, then choose the database from the picker that appears -- each option shows the database's name, whether it's Active or Inactive, and its current watch-item count, so there's no need to look up an internal ID first. The command applies the repository's safety and ownership validation.

Database operations are server-scoped. A server must not access or change another server's databases.

### Adding suggestions

`/add title:<text> [imdb_url] [release_year]` accepts a plain title, an IMDb link (either pasted into `title` directly or given separately via `imdb_url`), and an optional release year. Any Watch Party member may use it.

**Database selection is contextual.** WASH determines the target database from where the command is run, following the same automatic-then-picker pattern as `/list` (see below): the current channel or thread's configured database is used automatically with no prompt, and a picker only appears when the context is ambiguous (more than one database configured, none matching this channel) -- WASH never guesses which database a command applies to.

**IMDb link normalization.** A supplied IMDb link is validated against common IMDb title URL variants (with or without `https://`/`www.`, with or without a trailing path/query) and stored in its canonical form: `https://www.imdb.com/title/tt1234567/`. A malformed link is rejected with a clear error before anything is saved. This normalization never contacts IMDb or any external service. Separately, and unrelated to this normalization step, `/add` also resolves basic metadata (runtime, genres, poster, etc.) through the OMDb API when `OMDB_API_KEY` is configured -- that lookup is pre-existing behavior this milestone did not change.

**Duplicate detection.** Before a suggestion is saved, WASH checks the target database's active, archived, and vote-winner items for a match:

- An **IMDb ID match**, or a **matching title and release year**, is a *definite* duplicate.
- A matching title where either side's release year is unknown is a *possible* duplicate -- WASH never guesses.

An active-item match blocks with "🔴 That title is already in this collection.", followed by the matched item's reference (`Reference #0007`), title, IMDb link (if any), and status. What happens next depends on the matched item's status and who's asking:

| Matched item's status | Regular Watch Party member | WASH Crew |
| --- | --- | --- |
| Active (on the list already) | Blocked. Reference, title, IMDb link, and status are shown. | Blocked -- there is nothing to reactivate. |
| Archived (rejected via "I WILL NOT WATCH") | Blocked. | May confirm to reactivate the existing record. |
| Vote Winner | Blocked. | May confirm to reactivate the existing record. |
| Archived some other way (e.g. via `/remove`) | Blocked. | May confirm to reactivate the existing record. |
| Possible duplicate (no confirmed year) | Blocked. | May confirm to proceed with a new suggestion. |

Reactivating always reuses the existing record's stable ID and full history (rejections, watch dates, vote appearances) rather than creating a second entry -- nothing is ever silently overwritten.

**Confirmation posts.** The command's own acknowledgment is always ephemeral. If the target database has a suggestion channel configured, WASH posts (or, for a reactivation, updates) a public confirmation there showing the title, release year, canonical IMDb link, and reference number. If no suggestion channel is configured, the suggestion is still saved and the ephemeral reply explains that no public post was made. If posting fails (permissions, deleted channel, etc.), the suggestion is still preserved and the ephemeral reply explains the failure.

### Listing suggestions

`/list [status] [public]` is available to every Watch Party member. `status` selects **Available** (default), **Vote Winner**, or **Retired**. Only WASH Crew may set `public:true` to post the list in the channel; everyone else always sees it privately, including Vote Winner and Retired.

### Suggestion status model

Every suggestion shows one of five statuses, both in `/list` and on its own public confirmation post's Status field:

- 🟢 **Available** -- eligible for future voting.
- 🟡 **Rotation Cooldown** -- already presented in the database's current rotation; automatically returns to Available the moment a fresh rotation begins. This is computed at display time, not a separately stored value, so there is no manual "clear cooldown" step.
- 🟣 **Vote Winner** -- won a voting round; a watch party has not (yet, or ever) been marked complete for it.
- 🔵 **Watched** -- WASH has automatically confirmed the group watched it, via the Watch Party Lifecycle (see Section 6). This is a genuine, separately persisted status, not merely computed from Vote Winner.
- 🔴 **Retired** -- archived, whether by `/remove`, an "I WILL NOT WATCH" rejection threshold, or WASH Crew directly setting it via `/edit_suggestion`.

WASH Crew may always override a suggestion's status directly through `/edit_suggestion`'s Change Status action (Available, Vote Winner, Watched, or Retired -- Rotation Cooldown is never a directly settable option, since it's computed). Whenever a suggestion's status changes, its existing public confirmation post is edited in place to reflect the new status -- it is never recreated.

Database selection follows the same automatic-then-selector pattern used elsewhere: the current channel's configured database is used automatically; if none matches and the server has exactly one active database, that one is used; if several exist, WASH shows a picker. Each entry is a terse, at-a-glance line -- title and release year exactly once (`50 First Dates (2004)`, never `50 First Dates (2004) (2004)`), followed by `| [Original Suggestion](link)` when the original public post is known, or nothing after the title when it isn't. The reference number, status label, and IMDb link intentionally do not appear on this default view. Long lists page with Previous/Next buttons rather than being cut off or capped; both the initial response and every page suppress Discord's automatic link-preview embeds.

Older suggestions saved before public confirmation posts existed (or whose post failed at the time) have no original-post link to show and never will -- WASH has no reliable way to locate a Discord message after the fact without inventing a URL or risking a duplicate public post, so `/repair_suggestions` (WASH Crew-only; repairs legacy IMDb-link titles and a few known malformed records) does not attempt to recover it.

### Removing suggestions

`/remove query:<text>` is WASH Crew only. `query` may be a reference number (`#0007` or `7`), an exact title, or a title with its trailing "(YYYY)" year omitted. One match asks for confirmation before acting; several matches show a picker listing each candidate's reference, title, year, database, and status; no match reports that clearly. Confirmed removals **archive** the suggestion (its identity and full history are preserved) rather than deleting it -- see "Known limitations" for the one case where this isn't yet true everywhere.

### Editing suggestions

`/edit_suggestion reference:<text>` is WASH Crew only. `reference` is matched the same way `/remove` matches (reference number or exact title). It shows a read-only summary (title, release year, collection, status, IMDb link if any) alongside three actions:

- **Change Status** -- a dropdown of the four settable statuses (Available, Vote Winner, Watched, Retired; see "Suggestion status model" above). The suggestion's public confirmation post is updated in place once changed. Changing a Watched suggestion back to Vote Winner is also the correction workflow for a watch party WASH automatically marked complete in error: it removes that suggestion's most recently recorded watch date in the same action (see Section 6).
- **Move to Another Collection** -- a dropdown of this server's collections, so it's never necessary to type a raw ID. The destination must exist, be active, and belong to the same server. The same duplicate check `/add` uses runs again against the destination collection (excluding the suggestion's own record) -- a definite duplicate blocks the move, a possible one requires confirmation ("Move Anyway"). Moving preserves the suggestion's status, stable ID, journey, and history unchanged; only its collection (and an internal "last updated" timestamp) changes.
- **Cancel** -- makes no changes.

IMDb-derived fields (title, release year, IMDb link, director, etc.) are read-only here and no longer manually editable -- they always come from `/add`'s original OMDb lookup.

### New suggestion admission

Each database's `suggestion_rules.admission_mode` setting controls when a newly added (or reactivated) suggestion becomes selectable for voting:

- **Next Rotation** (the default) -- the suggestion is saved immediately but does not join whichever rotation is currently in progress. It's picked up automatically the next time a fresh rotation begins for that database.
- **Join Current Rotation** -- the suggestion is added to the in-progress rotation immediately, as unpresented, expanding the active pool live.

This setting only has a visible effect for databases using the Rotation Pool or Soft Rotation candidate-selection modes (see "Candidate selection and rotation management" below) -- Infinite Pool has no rotation concept for it to interact with.

### Known limitation: identical titles within one database

A "possible duplicate" warning is only ever raised because a candidate's title already matches an existing item's title (that's what makes it a candidate). Suggestion storage has always been keyed by (database, normalized title), so two records can never share an exactly-matching title in the same database. In practice this means confirming "add/save anyway" (or `/edit_suggestion`'s "Move Anyway") on a possible-duplicate warning succeeds only when the new title differs at all from every matched title -- confirming with a byte-for-byte identical title still reports the pre-existing "a suggestion with that title already exists" message instead of creating a second record. Changing this would mean changing how suggestions are identified in storage, which this milestone intentionally leaves alone.

## 4. Starting a Vote

Use `/vote start` to begin an interactive setup flow.

WASH offers:

- **Use Defaults**, which applies the configured candidate count, configured duration, and the server's configured default visibility.
- **Customize This Vote**, which accepts a candidate count and blind or visible voting (leaving either blank also uses the server's configured default, not a hardcoded value), plus a duration field using WASH's one shared duration syntax (see "Duration syntax" below) -- anywhere from 1 minute through 30 days, with an explicit unit always required (e.g. `10m`, `4h`, `3d` -- a bare `3` is no longer accepted). Practical shortcuts: **10m**, **30m**, **1h**, **12h**, **1d**, **1w**, or any custom value in that range. Every field's "leave blank to use the configured default" placeholder also names the actual value that will be used (e.g. "Leave blank to use the configured default (Visible)"), so nothing has to be guessed or looked up separately. A reminder-before-close override is also available here, using the same duration syntax with minute precision (e.g. `10m`, `1h`).

The target database is resolved the same contextual, automatic-then-picker way `/add` and `/list` resolve theirs (see Section 3) -- WASH never guesses when the channel is ambiguous. WASH then selects nominees from that database and creates an interactive voting post -- WASH's standard embed style with the yellow accent color, showing the round's visibility, duration, end time, and candidate titles (no leading nominee number; vote buttons below the embed carry the same clean titles). Candidate availability is validated before the round is created.

**Collection-centric titles.** Every voting message -- the post itself, the pre-close reminder, the closed record, the results announcement, a cancellation notice, and a deadline-change notice -- leads with the collection's name rather than the round number, e.g. "🎬 Movie Suggestions Voting Is Open" instead of "Voting Round 3 is Open". The round number is still shown as secondary information (a `Round` field on the voting post itself, and a `Round: 3` line on every other message). Movies, TV Shows, Anime, Holiday, Documentaries, and Horror collections automatically get a built-in emoji prefix (🎬, 📺, 🎌, 🎄, 🎞️, 🎃 respectively) based on their name; any other, custom-named collection displays with no emoji. A round created before a database was associated with it (or whose collection has since been removed) falls back to a generic, round-centric title with no collection name.

**Default voting visibility.** New servers default to **Visible**; **Blind** remains fully supported and selectable at any time via the Setup Wizard's Voting Defaults step or `/config`. An existing server's explicitly saved visibility (including one set to Blind) is never changed by this default; only a server configuration saved before this setting existed resolves to Visible.

**Default voting duration.** New servers default to **1 day**, configurable (1 minute through 30 days) via the Setup Wizard's Voting Defaults step or `/config`, both of which display it in natural language (e.g. "10 minutes", "4 hours", or "3 days" -- the largest whole unit it evenly divides into). An older server configuration that only ever saved a whole number of hours or days loads as the exact equivalent number of minutes, with no action required.

**Duration syntax.** Every relative duration WASH accepts -- vote duration, reminder-before-close, and `/vote edit`'s Shorten Vote/Extend Vote -- uses the same one syntax: a whole number immediately followed by a unit. Short forms `m`/`h`/`d`/`w` and the words `minute(s)`/`hour(s)`/`day(s)`/`week(s)` are both accepted (e.g. `10m`, `1h`, `3 days`, `1w`); a bare number with no unit is rejected. Vote duration supports the same minute precision as reminders and Shorten/Extend Vote -- `10m` and `30m` are perfectly valid vote durations, not just whole-hour amounts.

Only one open round is supported by the current voting service behavior.

### Candidate selection and rotation management

Each database's `suggestion_rules.candidate_selection` setting chooses how `/vote start` picks nominees from that database's eligible suggestions. It's configured through the Setup Wizard's Voting Defaults step (where, during first-time setup, exactly one collection exists so far) or, afterward, through `/config`'s Manage Collections section -- select the collection, then its Candidate Selection setting; both show and save the exact same value, chosen from a Discord dropdown rather than typed. The Setup Wizard and `/config` present these three modes under friendlier names; the underlying value in parentheses is what's actually persisted:

- **Balanced Random** (`rotation_pool`, the recommended and default choice) -- every eligible suggestion belongs to a rotation. Once presented in a vote, a suggestion is excluded from selection until the rotation is exhausted and a fresh one begins automatically.
- **Soft Rotation** (`soft_rotation`) -- unpresented suggestions are strongly preferred, but a previously presented suggestion remains technically eligible at a much lower selection weight rather than being excluded outright.
- **Pure Random** (`infinite_pool`) -- every eligible suggestion is always available; no rotation state is created or tracked for a database using this mode.

A server that never explicitly sets this (including one configured before this setting existed) keeps behaving exactly as Balanced Random/`rotation_pool` -- SuggestionRulesConfig's own documented default.

Within whichever pool a mode produces, WASH still applies its existing genre/media-type diversity pass and its existing deprioritization of recently nominated or recently won suggestions -- candidate-selection mode and diversity are independent, layered concerns.

**Rotation lifecycle.** A rotation tracks an identifier, its start and completion time, which suggestions were assigned to it, and which of those have been presented. A rotation completes once every assigned suggestion has reached one of: presented, Vote Winner, retired, or administratively archived/removed. Retired suggestions (see below) count toward completing a rotation but are never counted as presented. Rotation state is stored in its own JSON file under `data/` and is therefore covered automatically by `/backup`, `/restore`, and bot restarts, the same as every other repository.

A rotation also completes early -- before every assigned suggestion has reached one of those states -- whenever `/vote start` needs more candidates than the current rotation has left to present. A collection with plenty of "Available" suggestions can still have most of them on Rotation Cooldown from a recent round; rather than blocking the next vote until the rotation is fully exhausted, WASH starts a fresh rotation immediately, returning every non-Vote-Winner, non-Retired suggestion (including the ones just on cooldown) to eligibility. This only happens when doing so would actually help -- a rotation is never restarted while it can already supply the requested number of candidates, and a genuinely small collection is reported as having too few eligible suggestions rather than restarting pointlessly.

**Retired suggestions.** A suggestion reaching the "I WILL NOT WATCH" rejection threshold is *retired*, a distinct lifecycle from a WASH Crew-initiated `/remove` archive: WASH records a retirement date, reason, and (when known) the rotation it retired from. Retired suggestions leave the active rotation and are excluded from further selection, but remain visible through `/list status:Retired` and may later be reactivated through `/add`, exactly like any other archived suggestion.

**Low Pool Reminder.** When a database's active suggestion count falls to (or below) a configured threshold -- enabled by default, threshold 10 -- WASH posts a reminder to the database's suggestion channel (or a separately configured destination) naming the remaining count, the current rotation's completion percentage, and a nudge to use `/add`. The reminder respects a configurable minimum interval (24 hours by default) so it never fires more than once per interval regardless of how many suggestions are added or removed in between. It's currently evaluated only after a successful `/add`, since that's the moment a database's pool size most naturally changes.

### Known limitations: candidate selection

- **Retirement's originating rotation is usually unset.** The retirement record's rotation reference is only populated when rejection happens through the `/reject` command; the suggestion post's own "I WILL NOT WATCH" button (the primary way members reject a suggestion) doesn't yet carry rotation context through to it, so `retired_from_rotation_id` is `None` in the common case. The field itself is still recorded and available for a future milestone to populate more completely.
- **Likes, cooldowns, genre/runtime/franchise weighting, and statistics are not implemented.** The weighting architecture (`CompositeWeighting`/`WeightingFactor` in `services/candidate_selection_strategy.py`) exists specifically so a future milestone can add these without redesigning Soft Rotation or the selection pipeline, but no such factor exists yet beyond "has this been presented before."

## 5. Voting Operations

Community members vote by clicking a candidate's button on the interactive voting post -- there is no separate `/vote` slash command; casting a vote and changing it both go through the same buttons. `/vote status` (WASH Crew) reports the current round, with standings shown as candidate titles (e.g. `Happy Gilmore (1996) — 1 vote`), never the internal suggestion number; if a candidate's record is somehow missing, that entry falls back to its suggestion number rather than failing the command.

Current voting capabilities include:

- Blind or visible voting
- Configurable duration
- Vote-change limits
- Deterministic standings
- Tie support
- Persistent round storage
- Persistent interactive controls after restart

Automatic expiration, closing, and winner announcements are fully implemented, driven by the persistent scheduler rather than requiring a WASH Crew member to close a round manually.

**Changing a vote's end time.** `/vote edit`'s Change End Time action offers four options: **End Now**, **Shorten Vote**, **Extend Vote**, and **Set Exact End Time**.

- **End Now** closes the round immediately (with a confirmation prompt first, since this can't be undone).
- **Shorten Vote** and **Extend Vote** each open a submenu -- **1 Hour**, **1 Day**, or **Custom...** -- that adjusts the round's *current* end time by that amount (never relative to "now"): Shorten subtracts it, Extend adds it. **Custom...** opens a modal accepting any duration in WASH's shared syntax (e.g. `10m`, `1h`, `1d`, `1w`). Shortening past the current time is rejected with a clear message ("would move the end time into the past") rather than silently producing an already-closed-looking round.
- **Set Exact End Time** opens a modal with a single "Discord Timestamp" field (placeholder `<t:1785639600:F>`); its help text explains how to produce one -- type `@time` in any normal Discord message box, pick the desired date/time, then copy the generated timestamp here. WASH accepts `<t:unix>` and any of the standard styled variants (`<t:unix:F>`, `<t:unix:R>`, etc.), rejecting anything malformed or in the past with a clear message before anything changes.

Every path confirms with both a human-readable date/time and a Discord relative timestamp (e.g. "Saturday, August 1, 2026 8:00 PM (in 9 days)"), and reschedules the round's close/reminder jobs exactly as before. Every deadline is still stored and scheduled internally as UTC.

## 6. Watch Party Lifecycle

WASH manages the complete flow from a vote's winner to a confirmed Watched suggestion automatically, reusing the same scheduling foundation `/watch-party schedule` (below) has always used -- there is no second, parallel scheduling implementation.

### Scheduling from a vote's results

When a voting round closes with a single winner, its results post gains a **Schedule Watch Party** button (WASH Crew only). It already knows the collection, the winning suggestion, and every IMDb-derived detail (title, year, summary, runtime) -- clicking it opens a short modal asking only for what WASH doesn't already know: watch date/time, event duration (pre-filled from the winning title's runtime when known), a location, and an optional description override.

If the round produced a tie, the button instead reads **Choose Winner to Schedule**; selecting it shows a picker of the tied titles, then opens the same modal for whichever one is chosen.

Once scheduled, the button is replaced with a disabled **Watch Party Scheduled** state on that results post. A suggestion can never be scheduled twice this way -- if a non-cancelled watch party already exists for it, WASH shows that existing schedule instead of creating a second one (checked both before the modal opens and again immediately before saving, to close the race between the two).

### Discord Scheduled Events

Submitting the modal above does two things: it schedules the watch party inside WASH (as before), and it creates a real **Discord Scheduled Event** for it -- not just a WASH-internal record.

**What Discord's API actually supports.** Investigated directly against discord.py 2.7.1 and Discord's own documented behavior: there is no way for a bot to open Discord's native "Create Event" dialog pre-filled with data -- that dialog is client-side only, with no bot-facing equivalent. A bot can only create a Scheduled Event directly through the API. WASH does exactly that, which is the closest possible thing to the requested "native, pre-filled" experience: the real event, already fully filled in, rather than a prompt the administrator would still have to complete by hand.

**Event defaults.** Title is `🎬 Watch Party: <Title> (<Year>)` (e.g. "🎬 Watch Party: Oblivion (2013)"); description is the IMDb summary, then the IMDb link, then "Hosted by WASH" -- whichever parts are actually known. Discord requires an event to be either a Voice/Stage-channel event or an External one with a location; WASH always creates an **External** event, so it's never forced to guess which voice channel the group actually means -- the modal's **Location** field (free text, e.g. "Discord Voice Chat," a streaming link, "someone's living room") is the one thing WASH asks for beyond date/time and duration that it wouldn't otherwise need, purely because Discord's API requires *something* here for an External event. Date, time, and location otherwise remain entirely the administrator's choice, never guessed.

**Tracking and duplicate prevention.** The created event's ID is saved on WASH's watch party record, associated with the same collection/suggestion/winning vote the watch party itself already tracks. This is the same record duplicate-scheduling prevention (above) already checks -- a title can't end up with two Discord Events any more than it can end up with two WASH watch parties.

**Event synchronization.** WASH listens for the linked Discord Event being edited, cancelled, completed, or deleted directly in Discord (via the client, or any other tool) and updates its own record to match -- this is one-directional: Discord's event is the source of truth once it exists, and WASH follows it, never the other way around. A watch party rescheduled or cancelled through WASH's own `/watch-party reschedule`/`/watch-party cancel` does **not** push that change back onto the Discord Event -- edit or cancel the Discord Event itself directly if one exists (see "Known limitations" below).

### Automatic completion

**Finding:** Discord does not itself transition a Scheduled Event from "scheduled" to "completed" as its end time passes -- ending an event requires an explicit action (the Discord client's "End Event" button, or the same API WASH uses). That can't be relied on to reliably happen for a casual community watch party, so WASH's own schedule -- not the Discord Event's state -- remains the primary, reliable trigger: a watch party with a known duration completes automatically once it reaches its own scheduled end time, with no confirmation prompt. Completion marks the suggestion Watched, records the watch date, updates the suggestion's public post and statistics, updates watched history, and posts a completion announcement to the configured destination.

If the linked Discord Event *is* manually ended (or cancelled) before WASH's own schedule would have completed it, WASH reacts immediately instead of waiting -- whichever happens first wins, safely (both paths are idempotent, so there's no risk of double-processing if both eventually fire).

### Corrections

Sometimes a scheduled watch party doesn't actually happen. Rather than a dedicated "undo" screen, this reuses two existing workflows:

- `/edit_suggestion` -> Change Status -> **Vote Winner** (from a Watched suggestion) also removes that suggestion's most recently recorded watch date in the same action.
- `/watch-party reschedule` also lists watch parties WASH has already marked Watched, not only ones still Scheduled; rescheduling one reverts its status back to Scheduled.

Neither of these touches a linked Discord Event -- Discord doesn't allow reverting a Completed or Cancelled event back to Scheduled at all (that's an API-level restriction, not a WASH limitation), so a correction that involves an already-completed or already-cancelled Discord Event only fixes WASH's own record; create a fresh Discord Event by hand afterward if one is still wanted.

### `/watch-party schedule`, going forward

`/watch-party schedule` -- the pre-existing, manual scheduling command -- is unchanged and still fully available, and still does **not** create a Discord Scheduled Event (it never collected the details -- duration, location -- that doing so needs). It remains the right tool for a one-off or special event scheduled outside a normal vote (e.g. an ad-hoc rewatch, or a title that never went through voting). It does not currently collect a duration, so watch parties scheduled this way are **not** completed automatically; mark them Watched by hand via `/edit_suggestion` once they've happened. **Recommendation:** keep it available, unhidden, for exactly that manual/special-event case -- do not deprecate or hide it, since the automatic winner-driven flow above only covers suggestions that won a vote.

### Known limitations

- The **Schedule Watch Party** / **Choose Winner to Schedule** button works correctly for the lifetime of the running bot process (Discord's interactive-component dispatch tracks it automatically once posted), but it is not yet registered for restart-persistence the way voting buttons and watch-party reminder jobs are. If the bot restarts before a winner's button is clicked, that button goes stale; the watch party can still be scheduled manually via `/watch-party schedule` in the meantime.
- Creating the Discord Scheduled Event requires WASH's bot role to have the **Manage Events** permission (see the [Installation Guide](09-Installation-Guide.md)). Without it, scheduling still succeeds inside WASH -- only the Discord Event itself is skipped, with a clear message explaining why.
- A watch party rescheduled or cancelled through WASH's own commands does not edit or cancel its linked Discord Event -- see "Discord Scheduled Events" above.
- WASH never creates a Voice/Stage-channel event, only External -- see "Discord Scheduled Events" above for why.

## 7. Diagnostics and Integrity

`/diagnostics` no longer exists as a separate command -- its information was consolidated into `/about`, WASH's single status and information dashboard. Everyone gets WASH's identity and documentation links; WASH Crew additionally see:

- **Health** -- Discord connection quality, scheduler status, interactive voting restoration state, and whether OMDb (`OMDB_API_KEY`) is configured.
- **Configuration** -- the active collection, collection count, watch item count, scheduled watch party count, and whether a voting round is currently open.
- **Runtime** -- Python and discord.py versions, uptime, and the current server's name.

WASH also runs integrity checks against persisted data and writes operational information through the logging system. Review console and log output when startup reports an issue.

## 8. Data Storage

The current development build stores application data in JSON repositories for:

- Suggestion databases
- Suggestions and Watch Items
- Voting rounds

Historical voting rounds are retained. Direct JSON editing is not recommended because invalid or cross-referenced data can break application behavior.

Before manual maintenance, stop the bot and make a copy of the data files.

## 9. Current Maintenance Procedure

1. Confirm the full automated test suite passes before deployment.
2. Stop the running bot.
3. Back up the data directory manually (or run `/backup` beforehand).
4. update the checked-out repository.
5. Activate the virtual environment and install updated dependencies if needed.
6. Start WASH and review startup logs.
7. Run `/about` as a WASH Crew member to confirm Health, Configuration, and Runtime all look correct.
8. Smoke-test any commands changed in the release.

Automatic backups run on the schedule configured via the Setup Wizard or `/config` (see Section 10); manual `/backup` remains available at any time regardless of that setting. Import from another WASH instance is implemented via `/import`; that other instance's own `/backup` output is the "export" side of the exchange, so there is no separate export command.

## 10. Backup & Recovery

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

### Automatic backups

Automatic backups are **enabled by default and recommended**. They're configured through the Setup Wizard's Backup step or, afterward, `/config`'s Backup Defaults, either of which lets WASH Crew choose Enable (setting an interval in days and a retention count) or Disable. An existing configuration saved before this setting existed is treated as enabled, using the same defaults a new server gets.

While enabled, WASH creates a backup on the configured interval and prunes older automatic backups down to the configured retention count -- a separate count from any manual backups kept, since automatic and manual backups are tracked as distinct pools (the manifest's `kind` field records which pool each archive belongs to). Disabling stops future automatic backups from being created; it never deletes backups that already exist, and `/backup` remains fully available regardless of the setting. Re-enabling resumes scheduling from the saved (or newly configured) interval and retention count. Changing these settings through `/setup` or `/config` takes effect immediately, and the schedule is also reconciled against the saved configuration whenever WASH starts up.

### `/restore`

Restores WASH's entire dataset. The flow always is: select an existing local backup by filename **or** upload a `.zip` -> WASH validates it and shows a summary (application version, creation time, backup type, server ID, and whichever record counts it can determine -- suggestion databases, suggestions, vote rounds, membership requests, and whether a server configuration is present) -> WASH Crew explicitly clicks **Restore** or **Cancel**. Nothing is ever restored without that explicit confirmation, and validation never modifies live data.

Immediately before restoring, WASH creates a full safety backup of the current data using the same backup process. If that safety backup fails, the restore is aborted and live data is left untouched. If the restore step itself fails afterward, the safety backup is preserved and the failure message says so explicitly.

**A bot restart is recommended after any restore.** Several in-memory caches (suggestions, votes, membership requests) are only loaded once at startup; restored data on disk won't be reflected in a running bot's behavior until it restarts. `GuildConfiguration` reads are not cached and take effect immediately.

### `/database backup` and `/database restore`

Back up or restore a single suggestion database instead of everything. Run `/database backup`, then choose the database from the picker that appears (name, Active/Inactive status, and watch-item count are all shown) -- WASH produces a scoped backup containing only that database's record, its suggestions, and its configuration (not its vote history), attached as `Watch_Party_Manager_Database_Backup_<safe-database-name>_YYYY-MM-DD_HH-MM-SS.zip`.

`/database restore` requires choosing **Merge** or **Replace** explicitly -- WASH never infers which one you meant:

- **Merge** imports suggestions from the backup into the *existing* database with a matching ID. A suggestion whose title already exists for that database is skipped and reported as a conflict rather than overwritten. The destination database must already exist; Merge never creates one.
- **Replace** overwrites the selected database's own record and all of its suggestions with the backup's version (creating it fresh if it no longer exists), while leaving every other database and all other server data untouched. A full safety backup is made first, exactly as with `/restore`.

A single-database backup can only be restored back into the server it came from; WASH rejects a mismatch rather than silently importing another server's data.

### `/database reset`

Clears every suggestion (active and archived alike -- there is no separate archive store; both are just `WatchItem` records in the same file) from one suggestion database. The database record itself, its ID, its name, and its configuration are never touched, and no other database is affected.

Flow: run `/database reset` -> choose the database from the picker that appears (name, Active/Inactive status, and watch-item count are all shown) -> WASH shows how many suggestions would be removed -> click **Reset** -> a modal asks you to type `RESET` exactly (case-sensitive) -> WASH creates a full safety backup, then performs the reset. Clicking **Cancel**, or submitting anything other than `RESET`, leaves all data unchanged.

### `/factory_reset`

Removes every WASH-managed record belonging to the current server: server configuration, suggestion databases and their configuration, suggestions (including embedded watch history), vote rounds, membership requests, scheduled watch parties, and scheduled reminder jobs. Backup archives, `.env` files, the bot token, application code, the virtual environment, and logs are never touched -- this command only ever writes through WASH's own JSON repositories.

Flow: `/factory_reset` -> WASH shows a count of everything that would be removed -> click **Factory Reset** -> type `RESET` exactly -> a full safety backup is made, then the reset runs. Afterward, `/setup` is required again (removing the server's configuration is what makes WASH treat the server as never having been set up -- the same check `/setup` already used before this milestone).

### `/import`

Imports a backup produced by *another* WASH instance's own `/backup`. Unlike `/restore`, `/import` only ever accepts an uploaded `.zip` -- there is no "select an existing local backup" option, since the whole point is bringing in data WASH doesn't already have on disk.

Flow: upload the backup -> WASH validates it and shows the same kind of summary `/restore` shows -> choose **Merge**, **Replace**, or **Cancel** -> (Replace only) type `REPLACE` exactly -> a full safety backup is made, then the import runs.

Only "portable" data is ever imported: suggestion databases, their configuration, their suggestions, and vote rounds. This server's configuration -- its configured roles, channels, and server ID -- is **never** changed by an import, in either mode. Membership requests, scheduled reminders, and scheduled watch parties are also never imported, since they reference the *source* server's Discord channels/messages/approval history and would be meaningless (or actively misleading) here.

#### Merge versus Replace

Never inferred -- you always choose explicitly:

- **Merge**: a database whose name already exists locally (case-insensitive match) has its suggestions merged in; a suggestion whose title already exists for that database is skipped and reported as a conflict, never overwritten. Every other incoming database is imported as new. Numeric IDs from the other instance are meaningless here (each WASH instance assigns them independently), so they're reassigned automatically whenever they'd otherwise collide with something already local.
- **Replace**: every portable record currently belonging to this server is removed first, then the backup's portable data is imported fresh in its place. Other servers' data (in a hypothetical multi-server deployment) is untouched, and so is this server's own Discord role/channel configuration.

#### Import results

After an import completes, WASH reports databases and suggestions imported vs. skipped, any title conflicts detected, how many identifiers were reassigned to avoid collisions, and which categories of data were intentionally excluded. WASH does not keep a persistent history of past imports -- each result is only shown once, in that response.

### Restart requirement

**A bot restart is recommended after `/restore`, `/database restore`, `/database reset`, `/factory_reset`, or `/import`.** Several services (suggestions, votes, membership requests) load their data once at startup and cache it in memory; changes written to disk by any of these commands won't be reflected in a running bot's behavior until it restarts. `GuildConfiguration` reads are not cached, so configuration changes (including a factory reset requiring `/setup` again) take effect immediately even without a restart.

### Recommended backup strategy

- Run `/backup` before any release, dependency upgrade, or manual data edit.
- Run `/database backup` before experimenting with a specific database's suggestion rules or content.
- Keep at least one backup downloaded outside of WASH's own `data/backups/` directory (e.g. before a factory reset, since a factory reset's automatic safety backup still only lives in the same `data/` tree it's resetting).
- After using `/import`, review the reported conflicts and restart the bot before relying on the imported data.

### Troubleshooting

| Symptom | Cause | What happened to live data |
| --- | --- | --- |
| "This backup failed validation and cannot be restored" / "Import validation failed" | Corrupt ZIP, missing/unreadable manifest, unsafe path, or a checksum mismatch (tampered or truncated file). | Unchanged -- validation never writes anything. |
| "Unsupported backup type" | A full backup was offered to `/database restore`, a single-database backup was offered to `/restore` or `/import`, or an incompatible format version was found. | Unchanged. |
| "That backup was created in a different Discord server" | A `/database backup` archive's recorded server ID doesn't match the server `/database restore` was run in. | Unchanged. |
| "No existing suggestion database with that ID was found to merge into" | Merge was chosen but the destination database doesn't exist yet. | Unchanged -- use Replace instead if that's intended. |
| "N suggestion(s) were skipped as duplicates" (restore, reset, or import) | Merge detected a title already present in the destination database. | Only the non-conflicting suggestions were imported; nothing existing was overwritten. |
| "Confirmation text did not match ... exactly" | The typed `RESET`/`REPLACE` phrase didn't match, or didn't match case. | Unchanged -- nothing runs until the exact phrase is submitted. |
| "Safety backup failed, so the ... was aborted" (restore, reset, factory reset, or import) | WASH couldn't write the pre-action safety backup (e.g. disk full or permissions). | Unchanged -- the destructive action never began. |
| "Restore failed after the safety backup succeeded" | The safety backup was made, but copying the backup's files onto live data failed partway through. | The safety backup archive is intact and named in the error message; use `/restore` again with it if needed. |
| No suggestions appear after a successful restore/reset/import | A bot restart is required for the running process's in-memory cache to reflect the change (see "Restart requirement" above). | Data on disk is already correct; only the live bot's view of it is stale. |

## 11. Statistics & Reporting

`/stats [type] [public] [suggestion]` exposes read-only statistics derived entirely from existing historical data -- nothing is cached or incrementally counted; every value is recalculated from the suggestion, voting, rotation, and watch-party repositories each time the command runs.

### Statistic types

- **Server** (the default) -- watch parties, voting rounds (open/closed/cancelled, blind/visible, ties), participation, average candidates per round, and average vote duration.
- **Member** -- the requesting member's own suggestions submitted/watched/retired, votes cast, participation percentage, and winning suggestions. There is no way to target another member's statistics, by design.
- **Suggestion** -- one suggestion's created date, submitter, current status, nomination history (count, first/last nominated), watch/retirement history, and rotations participated in. `suggestion` accepts the same reference-number-or-exact-title matching `/remove` and `/edit_suggestion` use; multiple matches show a picker.
- **Rotation** -- the target collection's current rotation progress (presented/remaining/retired/watched/completion) plus historical rotation count, average duration, and average size. Collection selection follows `/list`'s automatic-then-picker pattern.
- **Collection** -- one collection's active/archived/watched/retired suggestion counts alongside its current rotation summary.

### Privacy

- Every Watch Party member may use `/stats`; every response is ephemeral by default, for every member including WASH Crew.
- WASH Crew may set `public:true` to post Server, Suggestion, Rotation, or Collection statistics publicly -- the same pattern `/list` already uses.
- **Member statistics are the one exception**: any member (not just WASH Crew) may set `public:true` to post their *own* member statistics publicly, since that's a self-consenting disclosure of their own data rather than an aggregate view. WASH Crew cannot retrieve or post another member's statistics under any circumstance -- there is no parameter to target one.
- A member's statistics remain fully available even after they leave the Watch Party role, since they're derived from Discord user IDs recorded on suggestions and votes, never from live role membership.

### Known limitation: submitter and creation-date tracking only covers suggestions added since this feature shipped

Member and suggestion statistics that depend on "who submitted this" or "when was this created" (suggestions submitted/watched/retired/winning per member; a suggestion's created date and days-until-first-nomination) rely on two fields -- `journey.original_suggester` and `journey.suggestion_date` -- that are recorded for the first time by this milestone, exclusively at the moment `/add` creates a brand-new suggestion. They are never modified afterward (not by reactivation, editing, or a database move) and are never backfilled onto suggestions that already existed. A suggestion added before this feature shipped simply has no recorded submitter or creation date, and its Suggestion statistics report those fields as unavailable rather than guessing; it's also excluded from every member's submission-based counts. Votes-cast-based statistics are unaffected, since `VoteRecord.discord_user_id` has always been recorded.

## 12. Planned Post-v1.0 Administration

Guided setup (`/setup`, rerunnable), rotation administration, statistics/reporting, and the Watch Party Lifecycle including Discord Scheduled Events integration (Section 6) are implemented -- see the sections above. Planned post-v1.0 enhancements include:

- Existing, newly created, or deferred watch-history destinations
- Event-series administration (the richer recurring-schedule model `docs/04-Data-Model.md` describes; scheduled watch parties today are a simpler, single-occurrence foundation -- see `domain/watch_party.py`)
- Pushing a WASH-initiated reschedule/cancellation back onto an already-linked Discord Event, and recreating a Discord Event as part of the Corrections workflow (see Section 6's "Known limitations")
- Retroactive/backdated watch-history entry for a watch party that was never scheduled through WASH at all
- Restart-persistence for the winner-announcement's Schedule Watch Party button (see Section 6's "Known limitations")
- Health and maintenance reporting

Until those features are implemented, `project_state.md` is authoritative about what administrators can use safely.
