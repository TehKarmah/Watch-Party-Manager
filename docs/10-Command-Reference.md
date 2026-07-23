# WASH Commands Reference

| Property | Value |
| --- | --- |
| Document | Commands Reference |
| File | `10-Command-Reference.md` |
| Version | 1.0 Draft |
| Status | Active Draft |
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
| `/about` | Everyone | View WASH info, version, latency, and uptime. |
| `/join_watch_party` | Everyone | Join or leave the Watch Party. |
| `/stats` | Watch Party Member | Show server, member, suggestion, rotation, or database statistics. |

`/stats` takes an optional `type` (Server, Member, Suggestion, Rotation, or Database; defaults to Server) and `public` option. Members may always post their own Member statistics publicly; posting any other type publicly requires WASH Crew.

## Watch Items

| Command | Required Role | Description |
| --- | --- | --- |
| `/add` | Watch Party Member | Add a watch item by title or IMDb link. |
| `/list` | Watch Party Member | List watch items by status. |
| `/remove` | WASH Crew | Remove a watch item. |
| `/edit_suggestion` | WASH Crew | Edit a suggestion's details or database. |

`/add` takes a `title` and optional `imdb_url` and `release_year`. `/list` takes an optional `status` (Available, Watched, or Retired; defaults to Available) and a `public` option (WASH Crew only).

## Voting

| Command | Required Role | Description |
| --- | --- | --- |
| `/start_vote` | WASH Crew | Start a new voting round. |
| `/vote_status` | WASH Crew | View the current voting round. |
| `/edit_vote` | WASH Crew | Change, end, or cancel the active vote. |

Casting a vote itself happens through the interactive buttons on the voting post, not a slash command.

## WASH Crew: Membership

| Command | Required Role | Description |
| --- | --- | --- |
| `/watch_party` | WASH Crew | Manage Watch Party membership. |

## WASH Crew: Configuration

| Command | Required Role | Description |
| --- | --- | --- |
| `/setup` | WASH Crew | Run the guided server setup wizard. |
| `/config` | WASH Crew | View or change WASH's server configuration. |

`/setup` is a one-time guided first-run flow; once setup is complete, `/config` edits individual settings section by section (roles, channels, the active suggestion database, suggestion post destination, watched-movie destination, voting/reminder/backup defaults) without repeating the whole wizard.

## WASH Crew: Suggestion Databases

| Command | Required Role | Description |
| --- | --- | --- |
| `/database_add` | WASH Crew | Create a suggestion database. |
| `/database_list` | WASH Crew | List this server's suggestion databases. |
| `/database_remove` | WASH Crew | Deactivate a suggestion database. |
| `/database_backup` | WASH Crew | Back up a single suggestion database. |
| `/database_restore` | WASH Crew | Restore a database backup. |
| `/database_reset` | WASH Crew | Clear one database's suggestions. |

## WASH Crew: Watch Parties

| Command | Required Role | Description |
| --- | --- | --- |
| `/watch_party_status` | WASH Crew | View the scheduled watch party. |
| `/schedule_watch_party` | WASH Crew | Schedule a watch party. |
| `/reschedule_watch_party` | WASH Crew | Change a watch party's start. |
| `/cancel_watch_party` | WASH Crew | Cancel a scheduled watch party. |

## WASH Crew: Maintenance

| Command | Required Role | Description |
| --- | --- | --- |
| `/repair_suggestions` | WASH Crew | Repair bad suggestion data. |
| `/backup` | WASH Crew | Create and download a WASH backup. |
| `/restore` | WASH Crew | Restore WASH's data from a backup. |
| `/factory_reset` | WASH Crew | Erase all WASH data for this server. |
| `/import` | WASH Crew | Import a backup from another WASH instance. |

## WASH Crew: Diagnostics

| Command | Required Role | Description |
| --- | --- | --- |
| `/diagnostics` | WASH Crew | Show WASH runtime diagnostics. |
