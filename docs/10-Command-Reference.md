# WASH Commands Reference

| Property | Value |
| --- | --- |
| Document | Commands Reference |
| File | `10-Command-Reference.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | July 2026 |
| Authors | TehKarmah & ChatGPT |

Every WASH slash command currently implemented, grouped by functional area, with the Discord role required to use it. This is the link `/help` points to in Discord (as a Commands Reference embed, not a raw GitHub link) so members always land on an accurate, complete list.

For explanations of WASH concepts (Blind Vote, Rotation Pool, watch item statuses, and so on) rather than a command list, see the [Expanded Help Guide](08-Expanded-Help.md).

Required Role reflects WASH's three-tier permission model:

- **Everyone** -- any server member, no configured role required.
- **Watch Party Member** -- the server's configured Watch Party role (or WASH Crew, which always inherits Watch Party Member capability).
- **WASH Crew** -- the server's configured WASH Crew role.

## General

| Command | Required Role | Description |
| --- | --- | --- |
| `/help` | Everyone | Show the WASH command guide. |
| `/about` | Everyone | View WASH's status, health, and configuration info. |
| `/join_watch_party` | Everyone | Join or leave the Watch Party. |
| `/stats` | Watch Party Member | Show server, member, suggestion, rotation, or collection statistics. |

`/stats` takes an optional `type` (Server, Member, Suggestion, Rotation, or Collection; defaults to Server) and `public` option. Members may always post their own Member statistics publicly; posting any other type publicly requires WASH Crew.

`/about` answers "tell me about this running instance of WASH": everyone sees WASH's identity and Documentation links; WASH Crew additionally see Health (Discord connection, scheduler, interactive-voting restoration, OMDb configuration), Configuration (active collection, collection/watch-item/scheduled-watch-party counts, whether a voting round is open), and Runtime (Python/discord.py versions, uptime, server name) -- the information the former, separate `/diagnostics` command used to show. There is no longer a standalone `/diagnostics` command.

## Watch Items

| Command | Required Role | Description |
| --- | --- | --- |
| `/add` | Watch Party Member | Add a watch item by title or IMDb link. |
| `/list` | Watch Party Member | List watch items by status. |
| `/remove` | WASH Crew | Remove a watch item. |
| `/edit_suggestion` | WASH Crew | Change a suggestion's status or move it to another collection. |
| `/reject` | Watch Party Member | Mark a suggestion "I WILL NOT WATCH". |
| `/unreject` | Watch Party Member | Remove your own rejection from a suggestion. |

`/add` takes a `title` and optional `imdb_url` and `release_year`. `/list` takes an optional `status` (Available, Vote Winner, Watched, or Retired; defaults to Available) and a `public` option (WASH Crew only). `/reject` and `/unreject` both take a `suggestion_id` and mirror the suggestion post's own "I WILL NOT WATCH" button; results are always ephemeral, since a member's rejection is for their eyes only. `/reject`'s confirmation links back to the suggestion's original public post (when it has one) and includes an **Undo Rejection** button, restricted to the rejecting member, that immediately removes the rejection and refreshes the post's rejection count -- a quicker path than running `/unreject` separately. `/edit_suggestion` takes only a `reference` and then presents Change Status, Move to Another Collection, or Cancel -- IMDb-derived details (title, release year, IMDb link, etc.) are read-only and shown for reference, never manually edited. Change Status offers a dropdown of the four settable statuses (Available, Vote Winner, Watched, Retired); Move to Another Collection offers a dropdown of this server's collections -- neither requires typing a raw ID. Changing a Watched suggestion back to Vote Winner is the correction workflow for a watch party that was scheduled but didn't actually happen: it also removes that suggestion's most recent recorded watch date in the same action.

## Voting

| Command | Required Role | Description |
| --- | --- | --- |
| `/vote start` | WASH Crew | Start a new voting round. |
| `/vote status` | WASH Crew | View the current voting round. |
| `/vote edit` | WASH Crew | Change, end, or cancel the active vote. |

Casting a vote itself happens through the interactive buttons on the voting post, not a slash command. When a vote closes with a single winner, its results post gains a **Schedule Watch Party** button (WASH Crew only) that opens a short modal (watch date/time, duration, location, optional description) already knowing the winning title -- this is the normal way to schedule a watch party. A tie instead shows **Choose Winner to Schedule**, letting WASH Crew pick which winning title to schedule. Once scheduled, the button becomes a disabled **Watch Party Scheduled** state; scheduling the same winner twice is prevented.

Submitting the modal creates a real Discord Scheduled Event (not just a WASH-internal record) -- see the next section.

## WASH Crew: Membership

| Command | Required Role | Description |
| --- | --- | --- |
| `/watch_party` | WASH Crew | Manage Watch Party membership. |

`/watch_party` (underscore) manages *membership* -- who holds the Watch Party role. It's a distinct command from `/watch-party` (hyphen, below), which manages the *scheduled watch party itself*. Discord treats the two as entirely separate, valid command names.

## WASH Crew: Configuration

| Command | Required Role | Description |
| --- | --- | --- |
| `/setup` | WASH Crew | Run the guided server setup wizard. |
| `/config` | WASH Crew | View or change WASH's server configuration. |

`/setup` is a one-time guided first-run flow; once setup is complete, `/config` edits individual settings section by section (roles, channels, Manage Collections -- pick a collection to edit its suggestion destination, watched-movie destination override, and candidate selection -- a server-wide default Watched Movie Destination, a Watch Party Announcement Destination, and voting/reminder/backup defaults) without repeating the whole wizard. The Watch Party Announcement Destination is where scheduled/reminder/completion/cancellation watch party announcements post -- separate from the Home Channel, a collection's suggestion thread, and the Watched Movie Destination; it defaults to the Home Channel and, like Home Channel, can never be a thread. Every collection must have exactly one dedicated suggestion destination, so Manage Collections can only change it to a different thread (never a plain channel, and never WASH's configured Home Channel), never clear it; a collection's watched-movie destination remains optional and may be cleared to fall back to the server-wide default. Backup defaults let WASH Crew enable (recommended, and the default) or disable automatic backups, and configure the interval/retention count when enabled -- `/backup` remains available regardless of this setting.

## WASH Crew: Collections

| Command | Required Role | Description |
| --- | --- | --- |
| `/database add` | WASH Crew | Create a collection. |
| `/database manage` | WASH Crew | Guided workflow: pick a collection, then choose what to do with it. |
| `/database list` | WASH Crew | List this server's collections. |
| `/database move` | WASH Crew | Move a collection's suggestion destination to a different thread. |
| `/database backup` | WASH Crew | Back up a single collection. |
| `/database restore` | WASH Crew | Restore a collection backup. |
| `/database remove` | WASH Crew | Deactivate a collection. |
| `/database reset` | WASH Crew | Clear one collection's suggestions. |

None of these take a raw ID parameter. `/database add` walks through a type choice (every standard collection type -- Movies, TV Shows, Anime, Holiday, Documentaries, Horror -- that this server doesn't already have a matching collection for, plus Special Collection and Custom, which are always available) and then a destination choice -- **Create New Thread** (Recommended; created under WASH's configured Home Channel, or the current channel if none is configured and it's a usable text channel), **Use Current Thread** (only enabled when the command was actually run inside a thread), or **Use Existing Thread** -- the same destination choice `/database move` offers. Collections live in threads only -- WASH's configured Home Channel can never itself become a collection's suggestion destination, whether via this choice, `/database move`, or `/config`. `/database move`, `/database backup`, `/database reset`, and `/database remove` all show a picker of this server's collections (name, Active/Inactive status, and watch-item count) to choose from instead of typing an ID. `/database move` changes only the chosen collection's suggestion destination -- its database ID, suggestions, statuses, vote history, rotation history, statistics, and every other setting are untouched, and its existing Discord suggestion posts stay exactly where they are; only suggestions added after the move post to the new destination.

`/database manage` is a guided alternative to the direct subcommands above: pick a collection, then choose **Move Collection**, **Edit Collection**, **Backup Collection**, **Restore Collection**, **Reset Collection**, **Remove Collection**, or **Cancel** from a menu. Move/Backup/Reset/Remove each launch the exact same flow as their direct subcommand; Edit Collection opens the same per-collection settings menu `/config`'s Manage Collections section already uses (Suggestion Destination, Watched Movie Destination, Candidate Selection); Restore Collection points at running `/database restore` directly, since Discord doesn't allow attaching a file upload from inside a menu. The direct subcommands remain available as shortcuts for experienced administrators -- `/database manage` doesn't replace them.

## WASH Crew: Watch Parties

| Command | Required Role | Description |
| --- | --- | --- |
| `/watch-party status` | WASH Crew | View the scheduled watch party. |
| `/watch-party schedule` | WASH Crew | Schedule a watch party. |
| `/watch-party reschedule` | WASH Crew | Change a watch party's start. |
| `/watch-party cancel` | WASH Crew | Cancel a scheduled watch party. |

`/watch-party reschedule` takes a `when` option; `/watch-party cancel` takes none. Neither takes a watch party ID -- both show a picker of scheduled watch parties (title and scheduled date/time) to choose from instead. `/watch-party reschedule`'s picker also includes watch parties WASH has already automatically marked Watched, since rescheduling one is also the correction workflow for a watch party that was scheduled but didn't actually happen -- doing so reverts it back to Scheduled. Note: rescheduling or cancelling this way updates WASH's own record only -- it does not edit or cancel a linked Discord Scheduled Event; edit or cancel that directly in Discord if one exists (see [Administration](05-Administration.md)'s Watch Party Lifecycle section).

A watch party scheduled with a known duration (via the **Schedule Watch Party** button, above) is completed automatically at its scheduled end time: WASH marks the suggestion Watched, records the watch date, updates the suggestion post and statistics, and posts a completion announcement -- no confirmation prompt. This still happens even if nobody ever starts or ends the linked Discord Event -- WASH's own schedule is what's actually relied on; if the Discord Event *is* manually ended or cancelled first, WASH follows suit immediately instead of waiting. `/watch-party schedule` is a separate, manual path for one-off or special events outside the normal vote-winner flow; it does not currently collect a duration or create a Discord Event, so watch parties scheduled this way are not automatically completed and must be marked Watched by hand (`/edit_suggestion` -> Change Status -> Watched) once they've happened.

## WASH Crew: Maintenance

| Command | Required Role | Description |
| --- | --- | --- |
| `/repair_suggestions` | WASH Crew | Repair bad suggestion data. |
| `/backup` | WASH Crew | Create and download a WASH backup. |
| `/restore` | WASH Crew | Restore WASH's data from a backup. |
| `/factory_reset` | WASH Crew | Erase all WASH data for this server. |
| `/import` | WASH Crew | Import a backup from another WASH instance. |
