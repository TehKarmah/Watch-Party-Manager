# WASH Commands Reference

| Property | Value |
| --- | --- |
| Document | Commands Reference |
| File | `10-Command-Reference.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | August 2026 |
| Authors | TehKarmah & ChatGPT |

Every WASH slash command currently implemented, grouped by functional area, with the Discord role required to use it. This is the link `/help` points to in Discord (as a Commands Reference embed, not a raw GitHub link) so members always land on an accurate, complete list.

For explanations of WASH concepts (Blind Vote, Nominee Selection, watch item statuses, and so on) rather than a command list, see the [Expanded Help Guide](08-Expanded-Help.md).

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
| `/stats` | Watch Party Member | Show server, member, suggestion, or collection statistics. |

`/stats` takes an optional `type` (Server, Member, Suggestion, or Collection; defaults to Server) and `public` option. Members may always post their own Member statistics publicly; posting any other type publicly requires WASH Crew.

`/about` answers "tell me about this running instance of WASH": everyone sees WASH's identity and Documentation links; WASH Crew additionally see Health (Discord connection, scheduler, interactive-voting restoration, OMDb configuration), Configuration (active collection, collection/watch-item/scheduled-watch-party counts, whether a voting round is open), and Runtime (Python/discord.py versions, uptime, server name) -- the information the former, separate `/diagnostics` command used to show. There is no longer a standalone `/diagnostics` command.

## Watch Items

| Command | Required Role | Description |
| --- | --- | --- |
| `/add` | Watch Party Member | Add a watch item by title or IMDb link. |
| `/list` | Watch Party Member | List watch items by status. |
| `/random watch` | Watch Party Member | Pick one random eligible watch item from a collection. |
| `/remove` | WASH Crew | Remove a watch item. |
| `/edit_suggestion` | WASH Crew | Change a suggestion's status or move it to another collection. |
| `/reject` | Watch Party Member | Mark a suggestion "I Won't Watch". |
| `/unreject` | Watch Party Member | Remove your own rejection from a suggestion. |

`/random watch` (a grouped subcommand of `/random`) resolves a collection the same way `/list` does (automatic from thread context when unambiguous; a picker when it isn't, offered again from the result screen via **Change Collection**), then shows **Pick Random Item** and **Add Filters** -- both private (ephemeral), visible only to the member who ran the command. Selection is always uniformly random over the collection's eligible pool (the same Available items `/list`'s Eligible for Voting and voting itself use) -- it never applies Favor New/Older Additions or any other nominee-selection weighting. Add Filters opens the same shared filter menu `/vote start`'s Custom Vote Filters uses, optionally narrowing the random pool by Genre, IMDb Rating, MPAA Rating, Actor, and/or one eligible member's suggestions (a Discord User Select, matched by stable Discord user ID -- never a display name; a valid member is the server owner, a WASH Crew member, or a current Watch Party member with at least one eligible suggestion, and an invalid choice shows a highly visible ⚠️ warning and disables **Pick Random Item** until resolved); any combination of the five may be active at once, shown in a scalable **Current Filters** summary, and they apply only to the current session -- they never change the collection's or guild's own configuration. Once **Pick Random Item** actually finds an item, the result is posted **publicly** in the channel -- the ephemeral setup screen that triggered it is replaced with a brief private hand-off note. The public result shows the chosen item with WASH's usual suggestion details (title, year, poster, genre, IMDb link, original suggester, and a link back to its original suggestion post when available), always makes clear it was chosen randomly, and offers **Pick Again**, **Change Filters**, **Change Collection**, and (when available) **View Original Suggestion**; only the member who ran `/random watch` may use these -- anyone else is told only the person who ran the command can. **Pick Again** rerolls the same public message in place, preserving every active filter (immediate repeats allowed, no hidden no-repeat state); **Change Filters**/**Change Collection** each open a fresh private follow-up screen, and any new item found from it is posted as its own new public result. `/random watch` is a discovery tool only: it never starts a voting round, changes a suggestion's status, marks anything watched, or changes any collection setting.

`/add` takes a `title` and optional `imdb_url` and `release_year`. `/list` takes an optional `status` (Active Watch Items, Eligible for Voting, In an Active Vote, Pending Crew Review, Vote Winners, Retired, Watched, or All Watch Items; defaults to Active Watch Items) and a `public` option (WASH Crew only). Active Watch Items = Eligible for Voting + In an Active Vote, shown together with a short summary line (e.g. "🟢 Eligible for Voting: 2", "🗳️ In an Active Vote: 3") before the mixed list; All Watch Items shows every status. Eligible for Voting, In an Active Vote, Active Watch Items, and All Watch Items are all resolved through the same authoritative eligibility calculation every other command uses (Rotation & Collection Health), computed entirely from each suggestion's own status and `VoteService` -- they can never disagree with what starting a vote would actually see. Every entry leads with its status emoji (🟢/🗳️/⚠️/🏆/✅/🗄️), and a Vote Winner entry additionally shows a `Won: <date>` line when a win date was recorded. When run inside a collection's thread, `/list` automatically uses that collection; a **Switch Collection** button is always offered (when more than one collection exists) to view a different one without re-running the command. `/reject` and `/unreject` both take a `suggestion_id` and mirror the suggestion post's own "I Won't Watch" button; results are always ephemeral, since a member's rejection is for their eyes only. `/reject`'s confirmation links back to the suggestion's original public post (when it has one) and includes an **Undo Rejection** button, restricted to the rejecting member, that immediately removes the rejection and refreshes the post's rejection count -- a quicker path than running `/unreject` separately. Once a suggestion reaches Pending Crew Review (its collection's configured "I Won't Watch" threshold reached), its rejections are frozen and only WASH Crew's Retire/Keep Active/Reset Rejections decision (Admin Channel) can change it. `/edit_suggestion` takes only a `reference` and then presents Change Status, Move to Another Collection, or Cancel -- IMDb-derived details (title, release year, IMDb link, etc.) are read-only and shown for reference, never manually edited. Change Status offers a dropdown of the three settable statuses (Available, Vote Winner, Retired); Move to Another Collection offers a dropdown of this server's collections -- neither requires typing a raw ID.

## Voting

| Command | Required Role | Description |
| --- | --- | --- |
| `/vote start` | WASH Crew | Start a new voting round. |
| `/vote status` | WASH Crew | View the current voting round. |
| `/vote edit` | WASH Crew | Change, end, or cancel the active vote. |

Casting a vote itself happens through the interactive buttons on the voting post, not a slash command.

`/vote start` offers **Use Defaults** or **Customize This Vote**. Customize This Vote resolves the target collection first, then shows one screen with dropdowns to optionally override the collection's Nominee Selection Mode and Vote Visibility for this one round only (never changing the collection's own saved setting), plus -- when the collection has eligible suggestions -- an **Edit Filters** button opening the same shared filter menu `/random watch`'s Add Filters uses, with a scalable **Current Filters** summary showing each filter's value: **Genre** (narrow nominees to one genre already present in the eligible pool's stored IMDb metadata, with each option showing its eligible count), **IMDb Rating** (an optional minimum and/or maximum, 0.0-10.0, set via a small modal), **MPAA Rating** (one content rating already present in the eligible pool), **Actor** (a searchable text field matched against each suggestion's stored cast, with a disambiguation picker if more than one actor matches), and **Suggestion Source** (narrow nominees to one eligible member's suggestions, via a Discord User Select -- a valid member is the server owner, a WASH Crew member, or a current Watch Party member, and must have at least one eligible suggestion; an invalid selection shows a highly visible ⚠️ warning and disables continuing until resolved). Every filter defaults to Any and any combination may be active at once; each editor offers the same standardized **Clear Filter** button to reset just that one filter and return to the menu immediately. Continuing (from either the filter menu or the overrides screen) opens a modal for candidate count, duration, and reminder overrides, then a **Review This Vote** summary (naming any active filters) with **Start Vote**/**Cancel** -- nothing is created until confirmed. If the fully filtered pool doesn't have enough eligible suggestions for the requested candidate count, the round is not created and the error names the active filter(s), the remaining count, and how to resolve it. Every Nominee Selection Mode option (here, the Setup Wizard, and `/config`) shows a short, plain-language description of what it does, and every visibility choice is explained as: **Visible** -- everyone can see vote totals while voting is active; **Blind** -- results stay hidden until voting closes. See [Administration: Custom Vote Filters](05-Administration.md#custom-vote-filters) for full details.

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

`/setup` is a one-time guided first-run flow; once setup is complete, `/config` edits individual settings section by section (roles, channels, Collections -- pick a collection to edit its suggestion destination, Watched Item Archive override, and nominee selection -- a server-wide default Watched Item Archive, Voting Defaults, "I Won't Watch" Settings, reminder/backup defaults, and the Eligible Pool Warning) without repeating the whole wizard. The main `/config` menu is a clean numbered list of section names only -- no descriptions; each section's own screen carries its explanatory text instead. Voting Defaults mirrors the Setup Wizard's own layout: Vote Visibility plus, when the server has a collection, that collection's Nominee Selection (one collection resolves automatically; more than one requires choosing which collection's Nominee Selection to edit first) -- saving updates Nominee Selection on that collection only, while candidate count, duration, and visibility always save guild-wide. "I Won't Watch" Settings is its own dedicated, similarly collection-scoped section (own Setup Wizard step too, immediately after Voting Defaults): Enable/Disable, and -- only when enabled -- a threshold (1-10, default 2); disabling omits the button from every suggestion post in that collection entirely and blocks `/reject`, without discarding any rejection history already recorded. Every collection must have exactly one dedicated suggestion destination, so Collections can only change it to a different thread (never a plain channel, and never WASH's configured Home Channel), never clear it; a collection's Watched Item Archive remains optional and may be cleared to fall back to the server-wide default. Backup defaults let WASH Crew enable (recommended, and the default) or disable automatic backups, and configure the interval/retention count when enabled -- `/backup` remains available regardless of this setting. The Eligible Pool Warning section lets WASH Crew enable/disable the warning, set a custom eligible-suggestion threshold (or restore the automatic default -- the guild's configured candidate count times 5), and choose its destination -- the Admin Channel (default) or the Watch Party Home Channel. It fires once when the eligible pool drops to or below the threshold, stays silent while it remains there, and automatically re-arms once the pool rises back above it.

## WASH Crew: Collections

Release UX & Command Surface Cleanup: `/database`'s top-level surface is now intentionally minimal -- **add**, **list**, **health**, and **manage** cover everything except restoring a backup (which needs its own command; see below). Moving, backing up, resetting, and removing a collection are no longer separate top-level subcommands -- every one of those actions is fully reachable through `/database manage`'s guided picker-then-menu workflow instead, with no functionality lost.

| Command | Required Role | Description |
| --- | --- | --- |
| `/database add` | WASH Crew | Create a collection. |
| `/database list` | WASH Crew | List this server's collections. |
| `/database health` | WASH Crew | Show a collection's eligibility and pool health. |
| `/database manage` | WASH Crew | Guided workflow: pick a collection, then choose what to do with it (move, edit, back up, reset, or remove). |
| `/database restore` | WASH Crew | Restore a collection backup. |

None of these take a raw ID parameter. `/database add` walks through a type choice (every standard collection type -- Movies, TV Shows, Anime, Holiday, Documentaries, Horror -- that this server doesn't already have a matching collection for, plus Special Collection and Custom, which are always available) and then a destination choice -- **Create New Thread** (Recommended; created under WASH's configured Home Channel, or the current channel if none is configured and it's a usable text channel), **Use Current Thread** (only enabled when the command was actually run inside a thread), or **Use Existing Thread**. Collections live in threads only -- WASH's configured Home Channel can never itself become a collection's suggestion destination, whether via this choice, Move Collection, or `/config`.

`/database manage` is the single guided entry point for every other collection-management action: pick a collection from a picker (name, Active/Inactive status, and watch-item count -- never a typed ID), then choose an action from a menu ordered administrative actions first, destructive actions last: **Edit Collection**, **Backup Collection**, **Move Collection**, **Restore Collection**, **Reset Collection**, **Remove Collection**, or **Cancel**. Edit Collection opens the same per-collection settings menu `/config`'s Collections section already uses (Suggestion Destination, Watched Item Archive, Nominee Selection -- "I Won't Watch" is shown there for reference but edited from its own dedicated `/config` section). Move Collection changes only the chosen collection's suggestion destination -- its database ID, suggestions, statuses, vote history, statistics, and every other setting are untouched, and its existing Discord suggestion posts stay exactly where they are; only suggestions added after the move post to the new destination. Reset Collection and Remove Collection are destructive and require typing a confirmation phrase, kept visually distinct (red/danger-styled buttons) from the administrative actions above them.

Restore Collection can't perform a restore itself -- Discord has no way to attach a file upload in response to a button or menu selection -- so it tells you to run `/database restore` directly instead, the only `/database` subcommand that remains a direct command for that reason. `/database restore` accepts a `mode` (Merge or Replace), and either a `backup_filename` (an existing local collection backup) or a `backup_file` upload -- exactly one of the two.

`/database health` (Rotation & Collection Health) reports one collection's eligibility breakdown -- Total Watch Items, Active Watch Items (Eligible for Voting + In an Active Vote, shown indented beneath it), Pending Crew Review, Vote Winners, Retired, Watched, the guild's configured candidate count, a **Next Vote** status (Ready / Insufficient Suggestions), and a **Low Pool Status** (Healthy / Almost Complete / Insufficient). Collection selection works exactly like `/list`'s (automatic from thread context, with a **Switch Collection** button). `/database health` never modifies any state -- it only ever reports the current, computed state, so simply checking health can never change what a subsequent `/vote start` sees.

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
