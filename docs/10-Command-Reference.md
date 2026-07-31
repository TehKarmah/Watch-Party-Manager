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

`/add` takes a `title` and optional `imdb_url` and `release_year`. `/list` takes an optional `status` (Active Watch Items, Eligible for Voting, In an Active Vote, Vote Winners, Retired, Watched, or All Watch Items; defaults to Active Watch Items) and a `public` option (WASH Crew only). Active Watch Items = Eligible for Voting + In an Active Vote, shown together with a short summary line (e.g. "🟢 Eligible for Voting: 2", "🗳️ In an Active Vote: 3") before the mixed list; All Watch Items shows every status. Eligible for Voting, In an Active Vote, Active Watch Items, and All Watch Items are all resolved through the same authoritative eligibility calculation every other command uses (Rotation & Collection Health), computed entirely from each suggestion's own status and `VoteService` -- they can never disagree with what starting a vote would actually see, and no filter ever bootstraps or advances any rotation state. Every entry leads with its status emoji (🟢/🗳️/🏆/🗄️/✅), and a Vote Winner entry additionally shows a `Won: <date>` line when a win date was recorded. When run inside a collection's thread, `/list` automatically uses that collection; a **Switch Collection** button is always offered (when more than one collection exists) to view a different one without re-running the command. `/reject` and `/unreject` both take a `suggestion_id` and mirror the suggestion post's own "I WILL NOT WATCH" button; results are always ephemeral, since a member's rejection is for their eyes only. `/reject`'s confirmation links back to the suggestion's original public post (when it has one) and includes an **Undo Rejection** button, restricted to the rejecting member, that immediately removes the rejection and refreshes the post's rejection count -- a quicker path than running `/unreject` separately. `/edit_suggestion` takes only a `reference` and then presents Change Status, Move to Another Collection, or Cancel -- IMDb-derived details (title, release year, IMDb link, etc.) are read-only and shown for reference, never manually edited. Change Status offers a dropdown of the three settable statuses (Available, Vote Winner, Retired); Move to Another Collection offers a dropdown of this server's collections -- neither requires typing a raw ID.

## Voting

| Command | Required Role | Description |
| --- | --- | --- |
| `/vote start` | WASH Crew | Start a new voting round. |
| `/vote status` | WASH Crew | View the current voting round. |
| `/vote edit` | WASH Crew | Change, end, or cancel the active vote. |

Casting a vote itself happens through the interactive buttons on the voting post, not a slash command.

`/vote start` offers **Use Defaults** or **Customize This Vote**. Customize This Vote first shows a dropdown to optionally override the target collection's Nominee Selection Mode for this one round only (never changing the collection's own saved setting), then a modal for candidate count, duration, visibility, and reminder overrides. Every Nominee Selection Mode option (here, the Setup Wizard, and `/config`) shows a short, plain-language description of what it does, and every visibility choice is explained as: **Visible** -- everyone can see vote totals while voting is active; **Blind** -- results stay hidden until voting closes.

## WASH Crew: Membership

| Command | Required Role | Description |
| --- | --- | --- |
| `/watch_party` | WASH Crew | Manage Watch Party membership. |

`/watch_party` (underscore) manages *membership* -- who holds the Watch Party role. This is distinct from scheduling an actual watch party event, which is not part of WASH's v1 user-facing command set.

## WASH Crew: Configuration

| Command | Required Role | Description |
| --- | --- | --- |
| `/setup` | WASH Crew | Run the guided server setup wizard. |
| `/config` | WASH Crew | View or change WASH's server configuration. |

`/setup` is a one-time guided first-run flow; once setup is complete, `/config` edits individual settings section by section (roles, channels, Collections -- pick a collection to edit its suggestion destination, Watched Item Archive override, and nominee selection -- a server-wide default Watched Item Archive, voting/reminder/backup defaults, and the Eligible Pool Warning) without repeating the whole wizard. Every collection must have exactly one dedicated suggestion destination, so Collections can only change it to a different thread (never a plain channel, and never WASH's configured Home Channel), never clear it; a collection's Watched Item Archive remains optional and may be cleared to fall back to the server-wide default. Backup defaults let WASH Crew enable (recommended, and the default) or disable automatic backups, and configure the interval/retention count when enabled -- `/backup` remains available regardless of this setting. The Eligible Pool Warning section lets WASH Crew enable/disable the warning, set a custom eligible-suggestion threshold (or restore the automatic default -- the guild's configured candidate count times 5), and choose its destination -- the Admin Channel (default) or the Watch Party Home Channel. It fires once when the eligible pool drops to or below the threshold, stays silent while it remains there, and automatically re-arms once the pool rises back above it.

## WASH Crew: Collections

| Command | Required Role | Description |
| --- | --- | --- |
| `/database add` | WASH Crew | Create a collection. |
| `/database manage` | WASH Crew | Guided workflow: pick a collection, then choose what to do with it. |
| `/database list` | WASH Crew | List this server's collections. |
| `/database health` | WASH Crew | Show a collection's rotation eligibility and pool health. |
| `/database move` | WASH Crew | Move a collection's suggestion destination to a different thread. |
| `/database backup` | WASH Crew | Back up a single collection. |
| `/database restore` | WASH Crew | Restore a collection backup. |
| `/database remove` | WASH Crew | Deactivate a collection. |
| `/database reset` | WASH Crew | Clear one collection's suggestions. |

None of these take a raw ID parameter. `/database add` walks through a type choice (every standard collection type -- Movies, TV Shows, Anime, Holiday, Documentaries, Horror -- that this server doesn't already have a matching collection for, plus Special Collection and Custom, which are always available) and then a destination choice -- **Create New Thread** (Recommended; created under WASH's configured Home Channel, or the current channel if none is configured and it's a usable text channel), **Use Current Thread** (only enabled when the command was actually run inside a thread), or **Use Existing Thread** -- the same destination choice `/database move` offers. Collections live in threads only -- WASH's configured Home Channel can never itself become a collection's suggestion destination, whether via this choice, `/database move`, or `/config`. `/database move`, `/database backup`, `/database reset`, and `/database remove` all show a picker of this server's collections (name, Active/Inactive status, and watch-item count) to choose from instead of typing an ID. `/database move` changes only the chosen collection's suggestion destination -- its database ID, suggestions, statuses, vote history, rotation history, statistics, and every other setting are untouched, and its existing Discord suggestion posts stay exactly where they are; only suggestions added after the move post to the new destination.

`/database manage` is a guided alternative to the direct subcommands above: pick a collection, then choose **Move Collection**, **Edit Collection**, **Backup Collection**, **Restore Collection**, **Reset Collection**, **Remove Collection**, or **Cancel** from a menu. Move/Backup/Reset/Remove each launch the exact same flow as their direct subcommand; Edit Collection opens the same per-collection settings menu `/config`'s Collections section already uses (Suggestion Destination, Watched Item Archive, Nominee Selection); Restore Collection points at running `/database restore` directly, since Discord doesn't allow attaching a file upload from inside a menu. The direct subcommands remain available as shortcuts for experienced administrators -- `/database manage` doesn't replace them.

`/database health` (Rotation & Collection Health) reports one collection's eligibility breakdown -- Total Watch Items, Active Watch Items (Eligible for Voting + In an Active Vote, shown indented beneath it), Vote Winners, Retired, Watched, the collection's own current rotation number and progress when it's still using a legacy Balanced Random/Soft Rotation mode (e.g. "Rotation 4 Progress: 18 of 27..." -- omitted for every other mode), the guild's configured candidate count, a **Next Vote** status (Ready / Insufficient Suggestions), and a **Low Pool Status** (Healthy / Almost Complete / Insufficient). Collection selection works exactly like `/list`'s (automatic from thread context, with a **Switch Collection** button). `/database health` never triggers a rotation rollover -- it only ever reports the current state, computed the same rotation-agnostic way `/list` computes it, so simply checking health can never change what a subsequent `/vote start` sees.

**Rotation Refresh Notification.** Whenever `/vote start` or `/list` actually rolls a completed rotation forward, WASH says so plainly ("All eligible watch items have now been presented. Starting Rotation 4.") -- as a follow-up message only the invoking WASH Crew member sees for `/vote start` (never on the public voting post itself), or as a leading line in `/list`'s own response.

## WASH Crew: Maintenance

| Command | Required Role | Description |
| --- | --- | --- |
| `/repair_suggestions` | WASH Crew | Repair bad suggestion data. |
| `/backup` | WASH Crew | Create and download a WASH backup. |
| `/restore` | WASH Crew | Restore WASH's data from a backup. |
| `/factory_reset` | WASH Crew | Erase all WASH data for this server. |
| `/import` | WASH Crew | Import a backup from another WASH instance. |

### Note on remaining underscore commands

`/join_watch_party`, `/edit_suggestion`, `/repair_suggestions`, and `/factory_reset` remain top-level, underscored commands rather than joining a group like `/database`/`/vote` (UI Polish review). Each was deliberately left as-is:

- **`/join_watch_party`** is a member-facing, everyone-can-use command, at least as frequently used as `/add`/`/list`/`/remove` -- exactly the kind of "short, commonly-used command" left unchanged. Folding it into the WASH Crew-only `/watch_party` (membership administration) group would also blur its "anyone can use this" identity with a group whose every other subcommand is WASH Crew-only.
- **`/edit_suggestion`** has no natural existing group to join without also regrouping `/add`, `/list`, and `/remove` -- which would contradict keeping those unchanged. A future, deliberate `/suggestion` group covering the whole Watch Item command family remains an option, but as one coordinated rename, not an isolated one.
- **`/repair_suggestions`** operates across every collection in the server at once, unlike every `/database` subcommand (always scoped to one chosen collection) -- joining that group would change its scoping semantics, not just its name.
- **`/factory_reset`** is a whole-server operation with no natural group sibling; it already sits consistently alongside `/backup`/`/restore`/`/import`, none of which are grouped either.
