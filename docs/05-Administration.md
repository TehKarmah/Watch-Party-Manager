# Watch Party Manager

## Administration

| Property | Value |
| --- | --- |
| Document | Administration |
| File | `05-Administration.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | August 2026 |
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

### Guided collection management

Release UX & Command Surface Cleanup: `/database`'s top-level surface is intentionally minimal -- **add**, **list**, **health**, and **manage** cover everything except restoring a backup. Moving, editing, backing up, refreshing, recovering, resetting, and removing a collection are not their own top-level subcommands at all -- run `/database manage`, choose a collection from the picker (name, Active/Inactive status, and current watch-item count -- never a typed ID), then choose an action from a menu ordered administrative actions first, destructive actions last: **Edit Collection**, **Backup Collection**, **Move Collection**, **Restore Collection**, **Refresh IMDb Metadata**, **Recover Missing IMDb Links**, **Reset Collection**, **Remove Collection**, or **Cancel**. Destructive actions (Reset, Remove) are styled distinctly (red/danger buttons) so they're never mistaken for a routine action; Refresh IMDb Metadata and Recover Missing IMDb Links are both styled like the other administrative actions, since neither ever deletes or recreates anything.

- **Move Collection** changes only the chosen collection's suggestion destination, using the same Create New Thread/Use Current Thread/Use Existing Thread choice `/database add` offers. Its database ID, suggestions, statuses, vote history, statistics, and every other setting are untouched. Existing Discord suggestion posts are never moved or edited; only suggestions added after the move post to the new destination. The chosen destination must not already be routed to another collection, must not be WASH's configured Home Channel, and a failure after a new thread is created (e.g. a duplicate destination) automatically deletes that thread rather than leaving it orphaned.
- **Edit Collection** opens the same per-collection settings menu `/config`'s Collections section already uses -- Suggestion Destination, Watched Item Archive, Nominee Selection -- with its Back button returning to this menu instead of `/config`'s own collection list.
- **Backup Collection** and **Reset Collection** are covered in [Backup & Recovery](#9-backup--recovery) below.
- **Restore Collection** can't be driven from a button click at all (Discord doesn't allow attaching a file upload in response to one), so it points at running `/database restore` directly instead -- the one collection-management action that remains its own top-level command, the same way the Setup Wizard's "Import Existing Database" option points at `/import`.
- **Refresh IMDb Metadata** is covered in its own section, [IMDb Metadata Refresh](#imdb-metadata-refresh), below.
- **Recover Missing IMDb Links** is covered in its own section, [IMDb Metadata Recovery](#imdb-metadata-recovery), below.
- **Remove Collection** deactivates the collection (see below), applying the repository's safety and ownership validation.

**Context Resolution Audit (bug fix):** every command that resolves "which collection applies here" (`/add`, `/list`, `/vote start`, `/stats`, and `/database add`'s own duplicate-channel check) shares one implementation, `resolve_database_for_channel`, itself built on the same single-channel resolver (`resolve_collection_channel_id`) `/database list` and `/config` already used for display. Previously, a moved collection's *original* channel kept resolving alongside its new one -- running a command from the old location could still silently reach the collection that had just been moved away from it. A collection's original channel now stops resolving the moment it's moved; only its current destination does, and that destination is also immediately free for a different collection to move into. This takes effect immediately (no restart needed) and survives one, since nothing about it is cached in memory -- resolution is always computed fresh from the same persisted records `/database list`/`/config` already read.

### Remove a database

Deactivating a collection (Remove Collection, under `/database manage`) applies the repository's safety and ownership validation before deactivating it.

Database operations are server-scoped. A server must not access or change another server's databases.

### IMDb Metadata Refresh

Older suggestions may have been added before WASH captured every field it does today (cast, in particular, was added after many collections already had suggestions in them), before an OMDb key was configured, or their stored details may simply be out of date. **Refresh IMDb Metadata** (under `/database manage`, WASH Crew only) re-fetches IMDb-derived details for suggestions that already have a usable IMDb link, so older suggestions can catch up without re-adding them.

**Scope.** After choosing Refresh IMDb Metadata, pick a scope:

- **Refresh This Collection** -- only the collection currently selected in `/database manage`.
- **Refresh All Collections** -- every *active* collection in the *current* server only. An inactive (removed) collection is skipped, and a collection belonging to a different Discord server (on a WASH process hosting more than one) is never included or even considered, regardless of how many collections that other server has.

Both options are always shown, even when the server only has one active collection -- Refresh All Collections simply refreshes that one collection in that case.

**Confirmation.** Before anything happens, a confirmation screen shows the chosen scope, how many collections are in scope, how many suggestions are actually eligible (have a usable stored IMDb link) out of how many were considered, and states plainly that this will make IMDb/OMDb requests, that existing WASH history and statuses are never changed, and that it may take some time. **Start Refresh** begins the operation; **Back** returns to the scope choice; **Cancel** makes no changes. No external request happens before Start Refresh is clicked.

**What gets refreshed.** Only already-persisted, IMDb-derived fields: canonical title, year (as part of the canonical title, matching how a newly-added suggestion already stores it), plot/summary, poster, runtime, genres, IMDb rating, MPAA/content rating, director, and cast. If a freshly-fetched response doesn't include a value for one of these fields, the suggestion's existing value is kept as-is -- a field is never blanked out just because one lookup happened to omit it.

**What's preserved.** Everything else: the suggestion's reference/ID, original suggester, suggestion date, which collection it belongs to, its status (including Pending Crew Review), rejection records, vote and nomination history, winner history, watch history, its Discord channel/thread/message IDs, its Crew Review notification reference (if one is open), and any manually entered value not derived from IMDb. A suggestion is never recreated, never reposted as a new message, and never moved to a different collection by a refresh.

**Suggestions without a usable IMDb link** (never linked to IMDb at all, or an unparseable link) are skipped, not guessed at from their title -- they're counted separately as Skipped in the summary.

**Efficiency and safety.** Requests are made one at a time (never a burst of concurrent lookups), and a title shared by more than one suggestion (in one collection or across several) is only ever looked up once per refresh, then reused for every matching suggestion. A lookup that fails is retried once after a short delay before being counted as Failed; processing always continues with the next suggestion after a failure rather than stopping the whole operation. Suggestion-post edits (see "Suggestion posts" below) are also deliberately paced -- a brief, conservative delay is inserted before an edit that would otherwise land too soon after the previous one in the *same* Discord channel, since Discord's message-edit rate limit is bucketed per channel. Edits to different channels are never paced against each other. This reduces how often Discord's own 429 rate-limit retries are needed; it doesn't replace them -- an occasional retry can still happen and doesn't affect the final results.

**Progress and results.** The workflow stays ephemeral (visible only to the WASH Crew member who started it) throughout. It acknowledges immediately, then updates periodically (not on every single suggestion) with collections/suggestions processed and running Refreshed/Unchanged/Skipped/Failed/posts-updated counts. The final summary shows the same totals, plus a per-collection breakdown for Refresh All Collections; if that breakdown would make the message too long, the totals stay inline and the full breakdown is attached as a text file instead.

**Suggestion posts.** After a suggestion's stored metadata actually changes, its existing public confirmation post is refreshed in place (poster, runtime, genres, IMDb rating, content rating, director, and plot/summary, wherever the post already shows them) -- the same in-place-edit mechanism every other status change already uses, never a new post. An item whose metadata came back identical to what was already stored is left Unchanged and its post is not re-edited. A missing/deleted post or an inaccessible channel is reported separately as a post-sync failure and never rolls back the metadata update that had already succeeded.

**Interruption and reruns.** This isn't a persistent background job -- if the bot restarts mid-refresh, whatever had already been successfully saved stays saved. Simply running Refresh IMDb Metadata again picks up any remaining suggestions; a suggestion whose metadata is already current is reported as Unchanged, never re-created or duplicated, so rerunning after an interruption (or just running it again later) is always safe.

### IMDb Metadata Recovery

IMDb Metadata Refresh (above) only re-fetches metadata for suggestions that *already* have a usable, persisted IMDb link -- a suggestion that was added by plain title with no link at all has no way back into that workflow. **Recover Missing IMDb Links** (under `/database manage`, WASH Crew only) is the answer: it finds suggestions with no usable IMDb identifier, searches OMDb for the correct title using each suggestion's stored title and release year, and -- only once a WASH Crew member explicitly approves a specific candidate -- saves that identifier and immediately hands off to Refresh IMDb Metadata's own logic to populate cast, IMDb rating, MPAA rating, runtime, genres, poster, and plot right away. This is a deliberately separate workflow from Refresh IMDb Metadata, not a merged option on the same screen -- Refresh assumes a link already exists and never guesses one; Recovery is the only place WASH ever proposes a *new* IMDb link on a Crew member's behalf, and it never does so without an explicit decision.

**Scope.** After choosing Recover Missing IMDb Links, pick a scope:

- **Recover This Collection** -- only the collection currently selected in `/database manage`.
- **Recover All Collections** -- every *active* collection in the *current* server only, using the exact same guild/active scoping Refresh IMDb Metadata uses -- a collection belonging to a different Discord server (on a WASH process hosting more than one) is never included or even considered.

Both options are always shown, even when the server only has one active collection.

**Confirmation.** Before anything happens, a confirmation screen shows the chosen scope, how many collections are in scope, how many suggestions are actually missing a usable IMDb link out of how many were considered, and states plainly that this will search OMDb, that every proposed match must be explicitly approved before anything is saved, that an existing IMDb identifier is never overwritten, and that existing WASH history and statuses are never changed. **Start Recovery** begins the operation; **Back** returns to the scope choice; **Cancel** makes no changes. No external request happens before Start Recovery is clicked.

**Crew Review, one suggestion at a time.** For each suggestion missing a link, WASH searches OMDb using its stored title and (when known) release year:

- **One high-confidence match** (a search scoped to the stored year returned exactly one result) is shown for confirmation, with its title, year, and type (movie/series/etc., when OMDb reports it).
- **Several plausible matches** are shown as a selection list, each option distinguished by title, year, and type -- choosing one only *proposes* it; it still lands on the same confirmation screen below, never saving directly from the list.
- **No good match** shows a plain "no matches found" screen -- WASH never guesses from the title alone.

Every proposed match is shown with four options: **Accept** (save it and refresh this suggestion's metadata immediately), **Skip** (leave this suggestion unmatched -- it's never permanently suppressed and will appear again in a future recovery scan), **Search Again** (open a small form to search by a different title, optionally with a year, e.g. "The Thing 1982" or "The Thing (1982)" both work), or **Cancel Recovery** (stop the entire run -- every suggestion not yet reached is reported as Cancelled, not Skipped, in the final summary). If the suggestion's stored year doesn't match any result, WASH automatically falls back to a title-only search rather than silently giving up -- but flags every candidate from that fallback with a visible note, so a Crew member is never asked to silently accept a different year than what's on file.

**What's saved and what's preserved.** Accepting a match only ever adds the missing IMDb identifier and the same IMDb-derived fields Refresh IMDb Metadata updates (see above); it never touches the suggestion's reference/ID, original suggester, suggestion date, collection assignment, status (including Pending Crew Review), rejection records, vote/nomination/winner/watch history, Discord channel/thread/message IDs, or Crew Review notification reference. An existing IMDb identifier is never overwritten -- a suggestion that already has one belongs to Refresh IMDb Metadata, not Recovery, and never appears in a recovery scan at all. If the identifier saves successfully but the immediate metadata enrichment step happens to fail, the link is still saved (a future Refresh IMDb Metadata run, or the next recovery scan skipping right past it, will pick up the rest) -- Recovery's core job is attaching the identifier, not guaranteeing every field lands in the very same session.

**Progress and results.** The workflow stays ephemeral throughout, visible only to the WASH Crew member who started it -- and only that member can act on it. Because every match needs a human decision, there's no automated batch progress bar the way Refresh IMDb Metadata has; each screen itself shows "Recovering N / M" so progress is always visible without a separate throttled update mechanism. The final summary shows overall totals (Matched, Skipped, Failed, Cancelled, suggestion posts updated, post-sync failures), plus a per-collection breakdown for Recover All Collections; if that breakdown would make the message too long, the totals stay inline and the full breakdown is attached as a text file instead.

**Suggestion posts.** After a match is accepted and its metadata refreshed, the suggestion's existing public confirmation post is updated in place through the exact same mechanism Refresh IMDb Metadata uses -- never a new post. A missing/deleted post or an inaccessible channel is reported separately as a post-sync failure and never rolls back the identifier or metadata that had already been saved.

**Interruption and reruns.** Not a persistent background job -- every suggestion successfully matched and saved before an interruption stays saved. A skipped suggestion, or one not yet reached before a Cancel Recovery or a restart, is simply picked up by running Recover Missing IMDb Links again -- nothing is permanently suppressed, and a suggestion that already gained a link along the way is correctly excluded from being offered a second time.

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
| Archived (retired after "I Won't Watch" review) | Blocked. | May confirm to reactivate the existing record. |
| Vote Winner | Blocked. | May confirm to reactivate the existing record. |
| Archived some other way (e.g. via `/remove`) | Blocked. | May confirm to reactivate the existing record. |
| Possible duplicate (no confirmed year) | Blocked. | May confirm to proceed with a new suggestion. |

Reactivating always reuses the existing record's stable ID and full history (rejections, watch dates, vote appearances) rather than creating a second entry -- nothing is ever silently overwritten.

**Vote Winner match view.** A Vote Winner match is shown differently from every other category: instead of a single line with a raw, unclickable IMDb URL, it shows its own block --

```text
Reference #0002
🏆 Vote Winner
Won: July 28, 2026
Original Suggestion
IMDb
```

-- with **Original Suggestion** linking back to the item's original public post and **IMDb** linking to its IMDb page, both as clickable Discord links. The `Won:` line and each link are shown only when the underlying data exists: a legacy Vote Winner with no recorded win date omits the `Won:` line, and a Vote Winner with no original post or no IMDb link simply omits that line too, rather than showing a placeholder. Every other matched category (active, archived-rejected, archived some other way) keeps the original single-line format unchanged.

**Confirmation posts.** The command's own acknowledgment is always ephemeral. If the target database has a suggestion channel configured, WASH posts (or, for a reactivation, updates) a public confirmation there showing the title, release year, canonical IMDb link, and reference number. If no suggestion channel is configured, the suggestion is still saved and the ephemeral reply explains that no public post was made. If posting fails (permissions, deleted channel, etc.), the suggestion is still preserved and the ephemeral reply explains the failure.

### Listing suggestions

`/list [status] [public]` is available to every Watch Party member. `status` selects **Active Watch Items** (default), **Eligible for Voting**, **In an Active Vote**, **Pending Crew Review**, **Vote Winners**, **Retired**, **Watched**, or **All Watch Items**. Only WASH Crew may set `public:true` to post the list in the channel; everyone else always sees it privately, including Vote Winners and Retired.

Active Watch Items = Eligible for Voting + In an Active Vote -- Vote Winners, Retired, and Watched are terminal statuses and are never part of Active. Active's own view shows a short summary before the mixed list, e.g.:

```text
🟢 Eligible for Voting: 2
🗳️ In an Active Vote: 3
```

All Watch Items shows every status at once, for a full-collection view.

Eligible for Voting, In an Active Vote, Active Watch Items, and All Watch Items are all resolved through `CollectionEligibilityService` -- the same authoritative eligibility calculation every other command uses (see "Nominee selection" below) -- so `/list` can never disagree with what starting a vote would actually see. This calculation is computed entirely from each suggestion's own status and `VoteService`. Vote Winners, Retired, and Watched are terminal buckets, so checking any of those filters alone is always side-effect-free. When run inside a collection's own thread, `/list` automatically uses that collection; a **Switch Collection** button (shown whenever more than one collection exists) lets a member view a different one without re-running the command with different context.

### Random Watch discovery

`/random watch` is available to every Watch Party member. It picks one item, uniformly at random, from a collection's Eligible Pool -- the same `Available` pool `/list status:Eligible for Voting` and `/vote start` both draw from, via the same `CollectionEligibilityService` -- to help a group that can't otherwise decide. It is a discovery tool only: it never starts a voting round, changes a suggestion's status, marks anything watched, or changes any collection setting.

Collection resolution matches `/list`: automatic from thread context when unambiguous, otherwise a picker; a **Change Collection** button on every screen returns to that picker without re-running the command. The initial screen offers **Pick Random Item** (true random, no filters) and **Add Filters**. Selection deliberately bypasses the collection's configured Nominee Selection mode entirely -- it is never Favor New Additions- or Favor Older Additions-weighted, even when the collection is configured that way -- so every eligible item has an equal chance regardless of collection settings. **Pick Again** performs a fresh, independent draw from the same pool; an immediate repeat of the same item is normal and expected, not a bug.

Add Filters opens the same shared filter menu `/vote start` uses (see "Custom Vote Filters" below): Genre, IMDb Rating, MPAA Rating, Actor, and Suggestion Source (Member), each optional, each independently resettable to "Any", applied in that fixed order and freely combinable. All five are per-session only -- clearing them, changing collections, or simply not using `/random watch` again never touches the collection's or guild's own configuration. If the active filter(s) leave no eligible item to pick from, the result names the collection and the active filter(s) rather than failing generically, and offers a way to change or clear filters or the collection.

The **Add Filters** screen and every collection picker stay private (ephemeral) -- only the member running `/random watch` ever sees them. Once **Pick Random Item** actually finds an item, the result is posted **publicly** in the channel for the whole group to see, with the ephemeral screen that triggered it replaced by a brief private hand-off note. The public result shows the chosen item using WASH's usual suggestion presentation (title, year, poster, genre, IMDb link, original suggester, and a link back to its original suggestion post when one exists), always states plainly that the pick was random, and offers **Pick Again**, **Change Filters**, **Change Collection**, and (when available) **View Original Suggestion**. Only the member who ran `/random watch` may use Pick Again/Change Filters/Change Collection -- anyone else clicking them gets WASH's standard "Only the person who ran this command can use these controls" message, matching how every other requester-scoped control in WASH already behaves. Pick Again rerolls the same public message in place, preserving every active filter; Change Filters and Change Collection each open a fresh *private* follow-up screen (a public message can never be edited into a private one), and a new item found from that follow-up is announced as its own new public message.

### Browse

`/browse` is WASH's interactive, paginated collection browser -- available to every Watch Party member, with three additional actions for WASH Crew. It is a browsing tool only: it never starts a vote automatically, changes a suggestion's status, modifies a collection, or edits metadata.

**Workflow.** Choosing a collection (automatic from thread context when unambiguous, otherwise a picker, matching `/list`/`/random watch`) opens the shared filter menu first -- results are never built or shown until **View Results** is explicitly clicked: `/browse` -> Collection -> Filter Menu -> **View Results** -> Results. When only one active collection exists, `/browse` lands directly on the filter menu (no separate collection-choice step). This keeps a large collection's results from ever being paginated needlessly before filters are even considered.

**Architecture.** `/browse` is another consumer of the exact same shared filter engine `/random watch` and Custom Vote already use -- it introduces no second filtering implementation. The eligible pool is `CollectionEligibilityService`'s own `Available` bucket, the identical pool `/random watch` and Custom Vote's Customize This Vote already draw from. The filter menu itself is the identical `FilterMenuView` session (Genre, IMDb Rating, MPAA Rating, Actor, Member, in that fixed order) and the identical **Current Filters** summary -- nothing about the filters themselves is Browse-specific. **View Results** applies the current filters, builds the filtered pool, and shows the paginated results screen -- the one and only place that pool/pagination is ever built.

**Results and pagination.** Unchanged from the results screen's own perspective, only its entry point moved. Each result line shows title, year, IMDb rating, MPAA rating, genres, who suggested it, its status, and its reference number. Results are paginated with **Previous**/**Next** and a page indicator; changing filters or changing collections always returns to page 1. Pagination reuses the same Discord-safe text-pagination component `/list` already uses, so a result page can never exceed Discord's message-length limit regardless of collection size. From the results screen, **Change Filters** returns to the filter menu (preserving every current filter and the selected collection), and **Change Collection** rebuilds the eligible pool and dynamic filter options for the newly chosen collection, then also returns to the filter menu -- it never jumps straight back to results. Filters are preserved wherever they still make sense: Genre, MPAA Rating, Actor, and Member are each cleared only if they no longer match anything in the new collection; a numeric IMDb Rating range is never cleared by a collection change, since it isn't tied to that collection's own enumerated values.

**WASH Crew actions.** Three additional buttons appear on the results screen for WASH Crew only -- never shown disabled to a non-Crew member, simply omitted:

- **🎲 Random Pick** -- a true-random draw from the *current filtered* results (never the whole collection, never an independently rebuilt pool), reusing `/random watch`'s own selection logic (`choose_random_watch_item`) and result presentation exactly, posted publicly the same way `/random watch`'s own Pick Random Item already is. The private browsing session underneath is left completely untouched.
- **🗳️ Start Vote** -- opens Custom Vote's own "Customize This Vote" screen (the same screen and code path `/vote start` -> Customize This Vote already uses), with the current collection already resolved and the current filters already carried over -- nothing has to be re-entered, and no filter is rebuilt from scratch. From there, Vote Settings, Nominee Selection, and Review This Vote all proceed exactly as they already do from `/vote start`.
- **📢 Post Publicly** -- posts the current page publicly (including the collection, active filters, and match count), with its own Previous/Next restricted to the WASH Crew member who posted it, using the same requester-only interaction model `/random watch`'s public result already uses. The ephemeral browsing session itself is never converted into a public message -- Post Publicly always creates a separate, dedicated public message alongside it.

**Empty results.** If **View Results** finds nothing (the active filters, or the collection itself, leave nothing to show), the paginated results screen never opens -- instead a plain message explains that no suggestions match the current filters, offering **Change Filters**, **Change Collection**, and **Back** (Back and Change Filters both return to the same filter menu) so the interaction always stays recoverable.

### Suggestion status model

Every suggestion shows one of six statuses, both in `/list` and on its own public confirmation post's Status field, in this order:

- 🟢 **Available** -- eligible for future voting.
- 🗳️ **In an Active Vote** -- currently a candidate in an open voting round for its collection. Computed at display time from `VoteService` -- there is no separately stored value, so it automatically reverts to Available the moment its round closes, with no manual "clear" step.
- ⚠️ **Pending Crew Review** -- reached once the collection's configured "I Won't Watch" threshold is met (see "Rejecting suggestions" below). Excluded from the Eligible Pool and its "I Won't Watch" button disabled until WASH Crew resolves it in the Admin Channel.
- 🏆 **Vote Winner** -- won a voting round. This replaces the older, never-actually-produced "Watched" status: WASH knows a suggestion won a vote, not that the group actually watched it. Whenever a Vote Winner is displayed with a recorded win date, an additional `Won: <Month D, YYYY>` line appears right below it (date only, never a time) -- the actual date `/vote start`'s vote completion recorded. A Vote Winner from before this milestone, with no recorded win date, simply omits that line rather than showing a placeholder.
- ✅ **Watched** -- explicitly confirmed watched, via the watch-history workflow.
- 🗄️ **Retired** -- archived, whether by `/remove`, WASH Crew retiring a suggestion pending Crew Review, or WASH Crew directly setting it via `/edit_suggestion`.

WASH Crew may always override a suggestion's status directly through `/edit_suggestion`'s Change Status action (Available, Vote Winner, or Retired -- In an Active Vote and Pending Crew Review are never directly settable options, since one is computed and the other is only reached via the "I Won't Watch" threshold). Whenever a suggestion's status changes, its existing public confirmation post is edited in place to reflect the new status -- it is never recreated.

### Rejecting suggestions ("I Won't Watch")

"I Won't Watch" has its own dedicated configuration, separate from Voting Defaults: a Setup Wizard step immediately after Voting Defaults, and a numbered `/config` section ("I Won't Watch" Settings). Both are per collection -- a server with exactly one collection edits it directly; more than one requires explicitly choosing which collection first, exactly like Nominee Selection.

Configuration is Enable/Disable, and -- only when enabled -- a threshold (default 2, range 1-10):

- **Enabled** (the default): every suggestion's public post has an **I Won't Watch** button, usable by any Watch Party member. The threshold is the number of distinct members who must press it before the suggestion needs WASH Crew review.
- **Disabled**: the button is omitted from every suggestion post entirely, and no new rejection is ever recorded. `/reject` also refuses with a clear message. Rejection history recorded while previously enabled is preserved for historical purposes but is never re-evaluated -- re-enabling only resumes accepting new rejections going forward, it never retroactively pushes an old below-threshold count into Pending Crew Review.

Reaching the threshold does not automatically retire the suggestion. Instead:

1. It's immediately removed from nominee eligibility.
2. Its status becomes ⚠️ Pending Crew Review.
3. Its "I Won't Watch" button is disabled.
4. The configured Admin Channel is notified with the suggestion, its collection, the current rejection count, and who voted "I Won't Watch".

The Admin Channel notification offers four actions:

- **Retire** -- permanently retires the suggestion (status becomes Retired).
- **Keep Active** -- restores Available status, clears every recorded "I Won't Watch" response, and re-enables the button.
- **Reset Rejections** -- leaves the suggestion Available, clears every recorded response, and re-enables the button.
- **View Suggestion** -- links back to the original suggestion post.

Only WASH Crew may use these actions. Below the threshold, a member may remove their own rejection (via the button again, or `/unreject`) at any time; once a suggestion reaches Pending Crew Review, its recorded rejections are frozen until WASH Crew resolves it. The Admin Channel notification's buttons -- like every other persistent control in WASH (voting, "I Won't Watch"/Watched, membership approval) -- continue working after a bot restart, without posting a duplicate notification or re-arming a review already resolved before the restart.

Database selection follows the same automatic-then-selector pattern used elsewhere: the current channel's configured database is used automatically; if none matches and the server has exactly one active database, that one is used; if several exist, WASH shows a picker. Each entry leads with its status emoji, followed by title and release year exactly once (`🟢 50 First Dates (2004)`, never `50 First Dates (2004) (2004)`), then `| [Original Suggestion](link)` when the original public post is known, or nothing after the title when it isn't. A Vote Winner entry additionally shows its `Won: <date>` line right below, when recorded. The reference number and IMDb link intentionally do not appear on this default view. Long lists page with Previous/Next buttons rather than being cut off or capped; both the initial response and every page suppress Discord's automatic link-preview embeds.

Older suggestions saved before public confirmation posts existed (or whose post failed at the time) have no original-post link to show and never will -- WASH has no reliable way to locate a Discord message after the fact without inventing a URL or risking a duplicate public post, so `/repair_suggestions` (WASH Crew-only; repairs legacy IMDb-link titles and a few known malformed records) does not attempt to recover it.

### Removing suggestions

`/remove query:<text>` is WASH Crew only. `query` may be a reference number (`#0007` or `7`), an exact title, or a title with its trailing "(YYYY)" year omitted. One match asks for confirmation before acting; several matches show a picker listing each candidate's reference, title, year, database, and status; no match reports that clearly. Confirmed removals **archive** the suggestion (its identity and full history are preserved) rather than deleting it -- see "Known limitations" for the one case where this isn't yet true everywhere.

### Editing suggestions

`/edit_suggestion reference:<text>` is WASH Crew only. `reference` is matched the same way `/remove` matches (reference number or exact title). It shows a read-only summary (title, release year, collection, status, IMDb link if any) alongside three actions:

- **Change Status** -- a dropdown of the three settable statuses (Available, Vote Winner, Retired; see "Suggestion status model" above). The suggestion's public confirmation post is updated in place once changed.
- **Move to Another Collection** -- a dropdown of this server's collections, so it's never necessary to type a raw ID. The destination must exist, be active, and belong to the same server. The same duplicate check `/add` uses runs again against the destination collection (excluding the suggestion's own record) -- a definite duplicate blocks the move, a possible one requires confirmation ("Move Anyway"). Moving preserves the suggestion's status, stable ID, journey, and history unchanged; only its collection (and an internal "last updated" timestamp) changes.
- **Cancel** -- makes no changes.

IMDb-derived fields (title, release year, IMDb link, director, etc.) are read-only here and no longer manually editable -- they always come from `/add`'s original OMDb lookup.

### New suggestion admission

A new suggestion is eligible for voting immediately, regardless of nominee-selection mode -- there is no admission delay or mode to configure. A reactivated suggestion (via `/add`) becomes eligible the same way.

### Known limitation: identical titles within one database

A "possible duplicate" warning is only ever raised because a candidate's title already matches an existing item's title (that's what makes it a candidate). Suggestion storage has always been keyed by (database, normalized title), so two records can never share an exactly-matching title in the same database. In practice this means confirming "add/save anyway" (or `/edit_suggestion`'s "Move Anyway") on a possible-duplicate warning succeeds only when the new title differs at all from every matched title -- confirming with a byte-for-byte identical title still reports the pre-existing "a suggestion with that title already exists" message instead of creating a second record. Changing this would mean changing how suggestions are identified in storage, which this milestone intentionally leaves alone.

## 4. Starting a Vote

Use `/vote start` to begin an interactive setup flow.

WASH offers:

- **Use Defaults**, which applies the configured candidate count, configured duration, the server's configured default visibility, and the target collection's own configured Nominee Selection Mode, unchanged.
- **Customize This Vote**, which resolves the target collection first (the same contextual, automatic-then-picker resolution described below), then shows one screen with: a dropdown to optionally override the collection's Nominee Selection Mode for this one round only (the collection's own saved setting is never changed by this), a Vote Visibility dropdown, and -- when the collection has eligible suggestions -- two further optional dropdowns for Custom Vote Filters (see below). Pressing **Continue to Vote Settings** then opens a modal accepting a candidate count and a duration field using WASH's one shared duration syntax (see "Duration syntax" below) -- anywhere from 1m-30d, with an explicit unit always required (e.g. `10m`, `4h`, `3d` -- a bare `3` is no longer accepted). Practical shortcuts: **10m**, **30m**, **1h**, **12h**, **7d**, or any custom value in that range. Every field's "leave blank to use the configured default" placeholder also names the actual value that will be used (e.g. "Leave blank to use the configured default (Visible)"), so nothing has to be guessed or looked up separately. A reminder-before-close override is also available here, using the same duration syntax with minute precision (e.g. `10m`, `1h`). Submitting the modal shows a **Review This Vote** summary (collection, Nominee Selection, Vote Visibility, candidate count, vote duration, and -- only when active -- Suggestion Source and Genre) with **Start Vote**/**Cancel** buttons; nothing is created, validated against the eligible pool, or announced until **Start Vote** is pressed.

The target database is resolved the same contextual, automatic-then-picker way `/add` and `/list` resolve theirs (see Section 3) -- WASH never guesses when the channel is ambiguous. WASH then selects nominees from that database and creates an interactive voting post -- WASH's standard embed style with the yellow accent color, showing the round's visibility, duration, end time, active filters (when any are set), and candidate titles (no leading nominee number; vote buttons below the embed carry the same clean titles). Candidate availability -- against the fully filtered pool, when Custom Vote Filters are in effect -- is validated before the round is created.

**Collection-centric titles.** Every voting message -- the post itself, the pre-close reminder, the closed record, the results announcement, a cancellation notice, and a deadline-change notice -- leads with the collection's name rather than the round number, e.g. "🎬 Movie Suggestions Voting Is Open" instead of "Voting Round 3 is Open". The round number is still shown as secondary information (a `Round` field on the voting post itself, and a `Round: 3` line on every other message). Movies, TV Shows, Anime, Holiday, Documentaries, and Horror collections automatically get a built-in emoji prefix (🎬, 📺, 🎌, 🎄, 🎞️, 🎃 respectively) based on their name; any other, custom-named collection displays with no emoji. A round created before a database was associated with it (or whose collection has since been removed) falls back to a generic, round-centric title with no collection name.

**Default voting visibility.** New servers default to **Visible**; **Blind** remains fully supported and selectable at any time via the Setup Wizard's Voting Defaults step or `/config`. An existing server's explicitly saved visibility (including one set to Blind) is never changed by this default; only a server configuration saved before this setting existed resolves to Visible. In plain terms: **Visible** means everyone can see vote totals while voting is active; **Blind** means results stay hidden until voting closes. This same explanation appears everywhere visibility is chosen or confirmed -- the Setup Wizard, `/config`, and `/vote start`'s Customize This Vote.

**Default voting duration.** New servers default to **1 day**, configurable (1m-30d) via the Setup Wizard's Voting Defaults step or `/config`, both of which display it in natural language (e.g. "10 minutes", "4 hours", or "3 days" -- the largest whole unit it evenly divides into). An older server configuration that only ever saved a whole number of hours or days loads as the exact equivalent number of minutes, with no action required.

**Duration syntax.** Every relative duration WASH accepts -- vote duration, reminder-before-close, and `/vote edit`'s Shorten Vote/Extend Vote -- uses the same one syntax: a whole number immediately followed by a unit. Short forms `m`/`h`/`d` and the words `minute(s)`/`hour(s)`/`day(s)` are both accepted (e.g. `10m`, `1h`, `3 days`); a bare number with no unit is rejected. Vote duration supports the same minute precision as reminders and Shorten/Extend Vote -- `10m` and `30m` are perfectly valid vote durations, not just whole-hour amounts.

Only one open round is supported by the current voting service behavior.

### Nominee selection

Each database's `suggestion_rules.candidate_selection` setting chooses how `/vote start` picks nominees from that database's eligible suggestions. It's configured through the Setup Wizard's Voting Defaults step (where, during first-time setup, exactly one collection exists so far), through `/config`'s Voting Defaults screen (which mirrors the Setup Wizard's own experience -- see below), or through `/config`'s Collections section -- select the collection, then its Nominee Selection setting. All three show and save the exact same value, chosen from a Discord dropdown rather than typed.

**`/config`'s Voting Defaults screen.** Nominee Selection is stored per collection, never guild-wide -- that never changes -- but `/config`'s Voting Defaults screen exposes it alongside the guild-wide Vote Visibility, candidate count, and vote duration so both concepts are edited from one place, matching the Setup Wizard's own layout. A server with exactly one collection resolves it automatically and preselects that collection's current Nominee Selection value; a server with more than one collection is asked to choose which collection's Nominee Selection this screen edits before showing it, with each option's own description naming its current mode (e.g. "Nominee Selection: Favor New Additions") so every collection's setting can be compared at a glance. Saving updates Nominee Selection on the selected collection only -- Vote Visibility, candidate count, and vote duration always save guild-wide, regardless of which collection was chosen. A server with no collections at all skips the Nominee Selection dropdown entirely. The main `/config` menu itself is a clean numbered list of section names with no descriptions -- explanatory text (including Vote Visibility's Blind/Visible explanation) lives on each section's own screen instead.

Three modes are offered, under friendlier names; the underlying value in parentheses is what's actually persisted:

- **Favor New Additions** (`favor_new_additions`, the recommended and default choice) -- weights eligible suggestions using each one's permanent suggestion date, favoring recently-added ones. Older suggestions stay eligible, just at a lower chance. *Leans toward suggestions added recently; older ones stay eligible, just at a lower chance.*
- **Favor Older Additions** (`favor_older_additions`) -- the mirror image of Favor New Additions: weights toward suggestions that have waited the longest, while newer ones stay eligible at a lower chance. *Leans toward suggestions that have waited the longest; newer ones stay eligible, just at a lower chance.*
- **Pure Random** (`infinite_pool`) -- every eligible suggestion is always available with equal weight. *Chooses completely at random from eligible suggestions, with no preference or exclusion.*

No mode ever permanently excludes a suggestion from selection.

The italicized sentence after each mode is the exact wording shown as that option's description everywhere it's chosen -- the Setup Wizard, `/config`'s Nominee Selection screen, and `/vote start`'s Customize This Vote -- so an administrator never has to already understand the underlying algorithm to pick one.

A server that never explicitly sets this defaults to Favor New Additions/`favor_new_additions` -- `SuggestionRulesConfig`'s own documented default.

Within whichever pool a mode produces, WASH still applies its existing genre/media-type diversity pass and its existing deprioritization of recently nominated or recently won suggestions -- nominee-selection mode and diversity are independent, layered concerns.

**Retired suggestions.** A suggestion WASH Crew retires from Pending Crew Review (see "Rejecting suggestions" above) is *retired*, a distinct lifecycle from a WASH Crew-initiated `/remove` archive: WASH records a retirement date and reason. Retired suggestions are excluded from further selection, but remain visible through `/list status:Retired` and may later be reactivated through `/add`, exactly like any other archived suggestion.

### Custom Vote Filters

`/vote start`'s **Customize This Vote** flow can optionally narrow which eligible suggestions are even considered before Nominee Selection runs, for that one round only, via an **Edit Filters** button that opens the same shared filter menu `/random watch`'s Add Filters uses (see "Random Watch discovery" above). Five independent filters are available, always shown in this fixed order (Member always last), and may be freely combined:

- **Genre.** Narrows the pool to eligible suggestions tagged with one chosen genre, drawn from IMDb metadata already stored on each suggestion at add time -- choosing a genre never triggers a new IMDb lookup. The dropdown only ever offers genres actually represented in the collection's current eligible pool, each showing its own eligible count (e.g. "Horror -- 7 eligible suggestions"). Matching is case-insensitive, and a suggestion with several genres matches if any one of them is the chosen genre; a suggestion with no genre metadata at all never matches while a genre filter is active.
- **IMDb Rating.** Narrows the pool to suggestions whose already-stored IMDb rating falls within an optional minimum and/or maximum (0.0-10.0, one decimal place, both inclusive; a minimum alone means "at least"; a maximum alone means "at most"; leaving both blank matches every rating). Set through a small modal (Discord selects can't collect free-text numbers), reached via a **Set Rating Range** button, with fields labeled Minimum IMDb Rating/Maximum IMDb Rating; a minimum entered above the maximum, or a value outside 0.0-10.0, is rejected with a clear inline message and the modal stays open to correct it. A suggestion with no stored rating, or one that can't be parsed as a number, never matches while this filter is active.
- **MPAA Rating.** Narrows the pool to suggestions tagged with one chosen content rating (e.g. G, PG, PG-13, R, NC-17, Not Rated, Unrated), drawn from ratings actually represented in the collection's current eligible pool. Matching is case-insensitive with whitespace trimmed; "Not Rated" and "Unrated" are kept as distinct options rather than merged, since OMDb uses them for different things (a title MPAA never rated, versus an alternate cut released without a new rating submission). A suggestion with no stored rating never matches while this filter is active.
- **Actor.** A searchable text field, not a capped dropdown -- open it, type part or all of an actor's name via the **Search Actor** button, and WASH searches every eligible suggestion's stored cast list (case-insensitive, matched against already-stored IMDb metadata, never a new lookup). One clear match applies immediately; several matches show a disambiguation picker naming each match (capped at 25, with a note to refine the search if more than 25 actually matched); no matches shows a clear warning and keeps the search field open to try again. A suggestion with no stored cast metadata never matches while this filter is active.
- **Suggestion Source (one member).** Narrows the pool to eligible suggestions originally submitted by exactly one chosen member -- useful for a themed round (e.g. a birthday vote) without needing any profile or birthday record. The member picker is a Discord User Select, so it works on servers with any number of members and cannot itself prevent an invalid choice from being selected -- WASH validates it afterward. A valid member is the **server owner**, a **WASH Crew member**, or a current **Watch Party member** (any one of the three qualifies) *and* has at least one eligible suggestion in the collection; a bot account is judged by these same two rules and is never rejected merely for being a bot. After choosing a member, WASH shows how many eligible suggestions they have: *"KC has 5 eligible suggestions."* An invalid choice -- someone who satisfies none of the three membership rules, or a valid member with zero eligible suggestions -- shows a highly visible warning (a leading ⚠️, the member's name, and the specific reason, e.g. *"⚠️ **HeidiTheGreat is not the server owner, a WASH Crew member, or a current Watch Party member.**"* or *"⚠️ **HeidiTheGreat has no eligible suggestions in "Movie Suggestions".**"*) and disables continuing until the member is changed or the filter is explicitly cleared back to Any Member -- it is never silently discarded while letting the flow continue. The warning clears immediately on a valid selection or on clearing the filter. Filtering matches a suggestion's stable Discord submitter ID, never a display name -- a suggestion made before WASH recorded submitters at all (see "New suggestion admission") has no recorded submitter and can never match any member filter.

Every filter defaults to **Any** (no filter) -- leaving a filter untouched, or explicitly resetting it, applies no filter and never forces an administrator through an extra screen. Every filter also treats a suggestion missing that field's metadata as never matching while the filter is *active*, but fully eligible again the moment it's reset to Any.

Every filter's own edit screen offers the same standardized layout: its picker (a dropdown, or a button opening a modal for IMDb Rating/Actor), a single **Clear Filter** button (worded and styled identically for every filter, replacing what used to be five separately-worded resets), then **Back**. Clicking Clear Filter resets only the filter currently being edited and returns immediately to the filter menu, whose display refreshes right away. That menu shows a scalable **Current Filters** block, dot-leader-aligned for quick scanning, e.g.:

```
Current Filters

Genre .......... Sci-Fi
IMDb Rating .... 7.0+
MPAA Rating .... Any MPAA Rating
Actor .......... Any Actor
Member ......... KC
```

The Customize This Vote screen (via Edit Filters) and `/random watch`'s Add Filters screen show this identical block, in the same fixed order, since both flows share one filter-editing module (`filter_menu_view.py`) and one validation service. Combining filters narrows to their intersection (e.g. Member = KC and Genre = Comedy selects only KC's eligible Comedy suggestions), and the chosen Nominee Selection mode still runs within whichever pool the filter(s) produce. If the filtered pool doesn't have enough eligible suggestions for the requested candidate count, the round is not created; the error names the active filter(s), how many eligible suggestions remain, how many nominees are required, and how to resolve it, e.g.:

> KC has 2 eligible suggestions, but this vote requires 3 nominees. Reduce the candidate count or choose another member.
>
> The Horror filter leaves 2 eligible suggestions, but this vote requires 3 nominees. Reduce the candidate count or choose another genre.

No filter is ever saved to the collection's own configuration -- a collection's saved Nominee Selection mode always remains one of the three standard modes (Favor New Additions, Favor Older Additions, Pure Random) described above, unaffected by any per-vote filter. When a filter is active, it's named in the **Review This Vote** summary before the round is created, and again -- alongside whichever Nominee Selection mode the round actually used -- in the voting post, `/vote status`, and the final results announcement, e.g.:

> Nominee Selection: Favor Older Additions
> Genre: Comedy
> IMDb Rating: 6.0–8.0
> Suggestion Source: KC

An inactive filter (Any Genre/Any IMDb Rating/Any MPAA Rating/Any Actor/Any Member) is never shown. Suggestion Source is stored as the member's stable Discord ID and displayed as a live `@mention`, so it always reflects that member's current nickname rather than a stale, saved display name. A `VoteRound` saved before IMDb Rating/MPAA Rating/Actor existed continues to load normally, with those three simply inactive.

Filters and Nominee Selection are deliberately separate, composable concerns: filters only ever narrow *which* suggestions are eligible for this one round, while Nominee Selection Mode decides *how* nominees are chosen from whatever pool results. The underlying filter architecture (`NomineePoolFilter`) is intentionally open-ended -- a future filter (e.g. runtime, release decade, director) is just one more class implementing the same interface plus one more entry in the shared filter menu, requiring no changes to `VoteService`, `NomineeSelectionService`, or the three existing Nominee Selection modes.

### Rotation & Collection Health

`CollectionEligibilityService` is the one authoritative implementation of "what suggestions are eligible right now" for a collection -- every command that needs an eligibility answer (`/vote start`, `/list`, `/database health`, and the Eligible Pool Warning below) calls it rather than computing its own. It's computed entirely from each suggestion's own status and `VoteService`. Its single `get_eligibility()` operation is always read-only.

It reports the same reconciled buckets for a collection: **Available** (the Eligible Pool -- selectable right now), **In an Active Vote** (currently nominated in this collection's open round), **Pending Crew Review**, **Vote Winners**, **Retired**, and **Watched**. These always reconcile: Active = Available + In an Active Vote, and Total = Active + Pending Crew Review + Vote Winners + Retired + Watched.

`/database health` (WASH Crew only) reports this breakdown for one collection: Collection Name, Total Watch Items, Active Watch Items, Eligible for Voting, In an Active Vote, Pending Crew Review, Vote Winners, Retired, Watched, the guild's Configured Candidate Count, a **Next Vote** status, and a **Low Pool Status**. Eligible for Voting and In an Active Vote are shown indented beneath Active Watch Items, so the reconciliation identity (Active Watch Items = Eligible for Voting + In an Active Vote) is visible directly in the layout, not just stated in a parenthetical. Collection selection works exactly like `/list`'s -- automatic from thread context, with a **Switch Collection** button when more than one collection exists. Checking health is always side-effect-free.

**Next Vote** is one of:

- **Ready** -- the collection has enough Active Watch Items overall to satisfy the configured candidate count.
- **Insufficient Suggestions** -- the collection doesn't have enough Active Watch Items to satisfy the candidate count; more suggestions need to be added.

**Low Pool Status** is one of **Healthy**, **Almost Complete**, or **Insufficient**, using the same threshold the Eligible Pool Warning below evaluates against -- Collection Health and the warning can never disagree about what "low" means.

### Eligible Pool Warning

WASH can proactively notify WASH Crew when a collection's eligible pool is running low, rather than requiring someone to check `/database health` or wait for `/vote start` to come up short. Configured per-server through `/config`'s Eligible Pool Warning section:

- **Enabled/Disabled** -- on by default.
- **Threshold** -- the number of eligible suggestions at or below which the warning fires. Defaults to the guild's configured candidate count times **5** (e.g. a candidate count of 3 gives a threshold of 15); a server may instead set a fixed custom threshold, or restore the automatic default.
- **Destination** -- the Admin Channel (default) or the Watch Party Home Channel.

The threshold is checked against the **Eligible Pool** (the same count `/database health`'s "Eligible for Voting" line shows) -- a flat multiple of the configured candidate count, the same regardless of collection size or nominee-selection mode.

**Re-arm behavior.** The warning dedups on threshold crossing: it fires once when the eligible pool drops to or at the threshold, stays silent on every later check while still at or below it, automatically re-arms the moment the pool rises back above the threshold, and fires again the next time it drops back down. This state is tracked in its own per-database file (see `persistence/eligible_pool_warning_state_repository.py`).

It's evaluated after a successful `/vote start` or `/add`, the moments a database's pool most naturally changes. The message names the collection, the eligible items remaining, and the warning threshold:

> **Eligible Pool Warning** -- Movie Night
> Eligible Items Remaining: 14
> Warning Threshold: 15
> Add more suggestions with `/add` followed by a title or IMDb link.

**Migrating from the old Rotation Low-Pool Notification.** Existing `low_suggestion_pool`/`low_suggestion_pool_threshold`/`low_suggestion_pool_destination` settings carry over unchanged -- a server with a previously saved custom threshold keeps that exact number; a server that never explicitly saved one now gets the new flat-multiple default instead of the old size-relative one. The enabled/disabled setting is preserved as-is.

### Known limitations: nominee selection

- **WASH still doesn't know when a group actually watches its winner.** A voting round's winner(s) are marked Vote Winner automatically (see "Suggestion status model" above), but that only records that a vote was won, not that the watch party actually happened. Confirming an actual viewing is left to a future watch-history milestone.
- **Likes, genre/runtime/franchise weighting, and statistics are not implemented.** The weighting architecture (`CompositeWeighting`/`WeightingFactor` in `services/candidate_selection_strategy.py`) exists specifically so a future milestone can add these without redesigning the selection pipeline, but no such factor exists yet beyond recency (Favor New/Older Additions).

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
- **Shorten Vote** and **Extend Vote** each open a submenu -- **1 Hour**, **1 Day**, or **Custom...** -- that adjusts the round's *current* end time by that amount (never relative to "now"): Shorten subtracts it, Extend adds it. **Custom...** opens a modal accepting any duration in WASH's shared syntax (e.g. `10m`, `1h`, `7d`). Shortening past the current time is rejected with a clear message ("would move the end time into the past") rather than silently producing an already-closed-looking round.
- **Set Exact End Time** opens a modal with a single "Discord Timestamp" field (placeholder `<t:1785639600:F>`); its help text explains how to produce one -- type `@time` in any normal Discord message box, pick the desired date/time, then copy the generated timestamp here. WASH accepts `<t:unix>` and any of the standard styled variants (`<t:unix:F>`, `<t:unix:R>`, etc.), rejecting anything malformed or in the past with a clear message before anything changes.

Every path confirms with both a human-readable date/time and a Discord relative timestamp (e.g. "Saturday, August 1, 2026 8:00 PM (in 9 days)"), and reschedules the round's close/reminder jobs exactly as before. Every deadline is still stored and scheduled internally as UTC.

## 6. Diagnostics and Integrity

`/diagnostics` no longer exists as a separate command -- its information was consolidated into `/about`, WASH's single status and information dashboard. Everyone gets WASH's identity and documentation links; WASH Crew additionally see:

- **Health** -- Discord connection quality, scheduler status, interactive voting restoration state, and whether OMDb (`OMDB_API_KEY`) is configured.
- **Configuration** -- the active collection, collection count, watch item count, scheduled watch party count, and whether a voting round is currently open.
- **Runtime** -- Python and discord.py versions, uptime, and the current server's name.

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
3. Back up the data directory manually (or run `/backup` beforehand).
4. update the checked-out repository.
5. Activate the virtual environment and install updated dependencies if needed.
6. Start WASH and review startup logs.
7. Run `/about` as a WASH Crew member to confirm Health, Configuration, and Runtime all look correct.
8. Smoke-test any commands changed in the release.

Automatic backups run on the schedule configured via the Setup Wizard or `/config` (see Section 9); manual `/backup` remains available at any time regardless of that setting. Import from another WASH instance is implemented via `/import`; that other instance's own `/backup` output is the "export" side of the exchange, so there is no separate export command.

## 9. Backup & Recovery

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

### Backup Collection and `/database restore`

Back up or restore a single suggestion database instead of everything. Choose **Backup Collection** from `/database manage`'s action menu, then pick the database from the picker that appears (name, Active/Inactive status, and watch-item count are all shown) -- WASH produces a scoped backup containing only that database's record, its suggestions, and its configuration (not its vote history), attached as `Watch_Party_Manager_Database_Backup_<safe-database-name>_YYYY-MM-DD_HH-MM-SS.zip`.

`/database restore` requires choosing **Merge** or **Replace** explicitly -- WASH never infers which one you meant:

- **Merge** imports suggestions from the backup into the *existing* database with a matching ID. A suggestion whose title already exists for that database is skipped and reported as a conflict rather than overwritten. The destination database must already exist; Merge never creates one.
- **Replace** overwrites the selected database's own record and all of its suggestions with the backup's version (creating it fresh if it no longer exists), while leaving every other database and all other server data untouched. A full safety backup is made first, exactly as with `/restore`.

A single-database backup can only be restored back into the server it came from; WASH rejects a mismatch rather than silently importing another server's data.

### Reset Collection

Clears every suggestion (active and archived alike -- there is no separate archive store; both are just `WatchItem` records in the same file) from one suggestion database. The database record itself, its ID, its name, and its configuration are never touched, and no other database is affected.

Flow: choose **Reset Collection** from `/database manage`'s action menu -> pick the database from the picker that appears (name, Active/Inactive status, and watch-item count are all shown) -> WASH shows how many suggestions would be removed -> click **Reset** -> a modal asks you to type `RESET` exactly (case-sensitive) -> WASH creates a full safety backup, then performs the reset. Clicking **Cancel**, or submitting anything other than `RESET`, leaves all data unchanged.

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

**A bot restart is recommended after `/restore`, `/database restore`, Reset Collection (`/database manage`), `/factory_reset`, or `/import`.** Several services (suggestions, votes, membership requests) load their data once at startup and cache it in memory; changes written to disk by any of these commands won't be reflected in a running bot's behavior until it restarts. `GuildConfiguration` reads are not cached, so configuration changes (including a factory reset requiring `/setup` again) take effect immediately even without a restart.

### Recommended backup strategy

- Run `/backup` before any release, dependency upgrade, or manual data edit.
- Run Backup Collection (`/database manage`) before experimenting with a specific database's suggestion rules or content.
- Keep at least one backup downloaded outside of WASH's own `data/backups/` directory (e.g. before a factory reset, since a factory reset's automatic safety backup still only lives in the same `data/` tree it's resetting).
- After using `/import`, review the reported conflicts and restart the bot before relying on the imported data.

### Troubleshooting

| Symptom | Cause | What happened to live data |
| --- | --- | --- |
| "This backup failed validation and cannot be restored" / "Import validation failed" | Corrupt ZIP, missing/unreadable manifest, unsafe path, or a checksum mismatch (tampered or truncated file). | Unchanged -- validation never writes anything. |
| "Unsupported backup type" | A full backup was offered to `/database restore`, a single-database backup was offered to `/restore` or `/import`, or an incompatible format version was found. | Unchanged. |
| "That backup was created in a different Discord server" | A Backup Collection (`/database manage`) archive's recorded server ID doesn't match the server `/database restore` was run in. | Unchanged. |
| "No existing suggestion database with that ID was found to merge into" | Merge was chosen but the destination database doesn't exist yet. | Unchanged -- use Replace instead if that's intended. |
| "N suggestion(s) were skipped as duplicates" (restore, reset, or import) | Merge detected a title already present in the destination database. | Only the non-conflicting suggestions were imported; nothing existing was overwritten. |
| "Confirmation text did not match ... exactly" | The typed `RESET`/`REPLACE` phrase didn't match, or didn't match case. | Unchanged -- nothing runs until the exact phrase is submitted. |
| "Safety backup failed, so the ... was aborted" (restore, reset, factory reset, or import) | WASH couldn't write the pre-action safety backup (e.g. disk full or permissions). | Unchanged -- the destructive action never began. |
| "Restore failed after the safety backup succeeded" | The safety backup was made, but copying the backup's files onto live data failed partway through. | The safety backup archive is intact and named in the error message; use `/restore` again with it if needed. |
| No suggestions appear after a successful restore/reset/import | A bot restart is required for the running process's in-memory cache to reflect the change (see "Restart requirement" above). | Data on disk is already correct; only the live bot's view of it is stale. |

## 10. Statistics & Reporting

`/stats [type] [public] [suggestion]` exposes read-only statistics derived entirely from existing historical data -- nothing is cached or incrementally counted; every value is recalculated from the suggestion, voting, and watch-party repositories each time the command runs.

### Statistic types

- **Server** (the default) -- watch parties, voting rounds (open/closed/cancelled, blind/visible, ties), participation, average candidates per round, and average vote duration.
- **Member** -- the requesting member's own suggestions submitted/watched/retired, votes cast, participation percentage, and winning suggestions. There is no way to target another member's statistics, by design.
- **Suggestion** -- one suggestion's created date, submitter, current status, nomination history (count, first/last nominated), and watch/retirement history. `suggestion` accepts the same reference-number-or-exact-title matching `/remove` and `/edit_suggestion` use; multiple matches show a picker.
- **Collection** -- one collection's active/archived/watched/retired suggestion counts.

### Privacy

- Every Watch Party member may use `/stats`; every response is ephemeral by default, for every member including WASH Crew.
- WASH Crew may set `public:true` to post Server, Suggestion, or Collection statistics publicly -- the same pattern `/list` already uses.
- **Member statistics are the one exception**: any member (not just WASH Crew) may set `public:true` to post their *own* member statistics publicly, since that's a self-consenting disclosure of their own data rather than an aggregate view. WASH Crew cannot retrieve or post another member's statistics under any circumstance -- there is no parameter to target one.
- A member's statistics remain fully available even after they leave the Watch Party role, since they're derived from Discord user IDs recorded on suggestions and votes, never from live role membership.

### Known limitation: submitter and creation-date tracking only covers suggestions added since this feature shipped

Member and suggestion statistics that depend on "who submitted this" or "when was this created" (suggestions submitted/watched/retired/winning per member; a suggestion's created date and days-until-first-nomination) rely on two fields -- `journey.original_suggester` and `journey.suggestion_date` -- that are recorded for the first time by this milestone, exclusively at the moment `/add` creates a brand-new suggestion. They are never modified afterward (not by reactivation, editing, or a database move) and are never backfilled onto suggestions that already existed. A suggestion added before this feature shipped simply has no recorded submitter or creation date, and its Suggestion statistics report those fields as unavailable rather than guessing; it's also excluded from every member's submission-based counts. Votes-cast-based statistics are unaffected, since `VoteRecord.discord_user_id` has always been recorded.

## 11. Planned Post-v1.0 Administration

Guided setup (`/setup`, rerunnable), nominee selection, and statistics/reporting are implemented -- see the sections above. Planned post-v1.0 enhancements include:

- Existing, newly created, or deferred watch-history destinations
- Event-series administration (the richer recurring-schedule/Discord Event model `docs/04-Data-Model.md` describes; scheduled watch parties today are a simpler, single-occurrence foundation -- see `domain/watch_party.py`)
- Scheduling and Discord Event publishing
- Historical corrections and retroactive watch-history entry
- Health and maintenance reporting

Until those features are implemented, `project_state.md` is authoritative about what administrators can use safely.
