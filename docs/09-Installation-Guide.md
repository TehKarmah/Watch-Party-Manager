# Watch Party Manager

## Installation Guide

| Property | Value |
| --- | --- |
| Document | Installation Guide |
| File | `09-Installation-Guide.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | July 2026 |
| Authors | TehKarmah & ChatGPT |

> [!NOTE]
> This guide is written for someone installing and configuring WASH for the first time, with no prior familiarity with the project. It assumes basic comfort with a terminal but nothing else. For command-by-command administration details once WASH is running, see [Administration](05-Administration.md). For a fast reference once you already know what you're doing, see the [README](../README.md)'s Quick Start.

## Table of Contents

1. Quick Start
2. Prerequisites
3. Get the Code
4. Python Environment Setup
5. Install Dependencies
6. Discord Developer Portal Setup
7. Configure `.env`
8. OMDb API Configuration (Optional)
9. Invite WASH to Your Server
10. Start WASH
11. Run the Setup Wizard
12. Server Configuration Overview
13. Installation Verification Checklist
14. Troubleshooting
15. Where to Go Next

---

## 1. Quick Start

For readers who just want the commands. Every step is explained in detail further down.

PowerShell:

```powershell
git clone https://github.com/TehKarmah/Watch-Party-Manager.git
cd Watch-Party-Manager
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item env.example .env
# Edit .env: set DISCORD_TOKEN (required). See Sections 6-8 below for how to obtain it.
python -m watch_party_manager.bot
```

macOS/Linux (bash/zsh):

```bash
git clone https://github.com/TehKarmah/Watch-Party-Manager.git
cd Watch-Party-Manager
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp env.example .env
# Edit .env: set DISCORD_TOKEN (required). See Sections 6-8 below for how to obtain it.
python -m watch_party_manager.bot
```

Once WASH is online in your server, run `/setup` as a server administrator to finish configuration (Section 11).

## 2. Prerequisites

| Requirement | Why |
| --- | --- |
| **Python 3.12 or later** | WASH is written for 3.12+ and is not tested against earlier versions. |
| **A Discord account** with permission to manage a server (or create a test server) | You'll create the bot application and invite it. |
| **Git** (optional) | Only needed if you clone the repository instead of downloading a ZIP. |
| **A terminal** | PowerShell on Windows, or any POSIX shell on macOS/Linux. |

Check your Python version:

```powershell
python --version
```

If that reports an older version and you have multiple Pythons installed, use the version-specific launcher shown in this guide (`py -3.12` on Windows, `python3.12` on macOS/Linux).

## 3. Get the Code

Clone the repository:

```powershell
git clone https://github.com/TehKarmah/Watch-Party-Manager.git
cd Watch-Party-Manager
```

If you don't have Git, download a ZIP of the repository from GitHub instead and extract it, then open a terminal in the extracted folder.

## 4. Python Environment Setup

WASH should always run inside a virtual environment, never installed into your system Python. This keeps its dependencies isolated from everything else on your machine.

PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

Your prompt should now show `(.venv)` at the start of the line. Every command in the rest of this guide assumes the virtual environment is active. If you close your terminal, reactivate it before continuing (re-run the `Activate.ps1` or `source .venv/bin/activate` line -- you don't need to recreate the environment).

## 5. Install Dependencies

With the virtual environment active:

```powershell
python -m pip install -e .
```

This installs WASH itself (in editable mode, so code changes take effect immediately) along with its two runtime dependencies, `discord.py` and `python-dotenv`, both pinned in `pyproject.toml`.

If you intend to run the automated test suite (see the [Developer Guide](06-Developer-Guide.md)), no separate test dependencies are required -- WASH's tests use Python's built-in `unittest`.

## 6. Discord Developer Portal Setup

WASH needs its own Discord Application and Bot User before it can connect to anything.

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and sign in.
2. Click **New Application**, give it a name (e.g. "WASH"), and create it.
3. Open the **Bot** tab and click **Add Bot** if it isn't already a bot.
4. Under **Privileged Gateway Intents**, leave everything **disabled** for a standard installation. WASH does not need Message Content, and does not require Presence. The **Server Members Intent** is optional -- see the note below.
5. Click **Reset Token** (or **Copy**, if this is the first time) to get your bot token. Treat this token like a password: never commit it, paste it publicly, or share it.

> [!NOTE]
> **Server Members Intent (optional).** WASH's `/stats server` command reports the server's current Watch Party member count and a participation percentage. Without the Server Members Intent enabled, Discord may not give WASH a complete member list, and that one figure can under-report. Every other feature works normally without it. If you want it, enable the toggle in this step; no other configuration in this guide changes.

### Required OAuth2 scopes and bot permissions

WASH is invited using the OAuth2 URL Generator described in Section 9. When you get there, select:

**Scopes:**

- `bot`
- `applications.commands` (required for slash commands to register)

**Bot Permissions:**

- View Channels
- Send Messages
- Embed Links
- Attach Files (used by `/backup`, `/database backup`, and `/import` to send/receive `.zip` files)
- Read Message History (used to edit WASH's own suggestion, vote, and confirmation posts)
- Manage Channels (the Setup Wizard's Home Channel and Admin Channel steps can create a new channel for you; without this permission, choose **Use Existing Channel** in those steps instead)
- Use External Emojis (optional, cosmetic only)

WASH does not need Manage Messages, Manage Roles, Administrator, or any moderation permission. It never moderates members or deletes other users' messages.

> [!IMPORTANT]
> **WASH must be added as a Guild Install, with both scopes selected together.** WASH is a traditional bot: it needs to actually join your server as a member to function -- it does not support Discord's User Install or command-only integration types, and nothing in WASH is built to work that way. If your Discord Application's **Installation** page (in the Developer Portal, separate from the OAuth2 URL Generator) has Guild Install scopes configured with `applications.commands` but not `bot`, slash commands can still appear to work in some contexts while WASH never actually joins the server -- which breaks anything that depends on WASH being a real member, including `/setup`'s first-run owner check. Confirm Guild Install is enabled and both `bot` and `applications.commands` are selected for it before inviting WASH.
>
> <img width="1339" height="558" alt="image" src="https://github.com/user-attachments/assets/f2b6e372-d1f6-4f0e-beec-6bfd91deee32" />


## 7. Configure `.env`

Copy the example environment file:

PowerShell:

```powershell
Copy-Item env.example .env
```

macOS/Linux:

```bash
cp env.example .env
```

Open `.env` in a text editor and fill in the values you need. **Never commit `.env`** -- it holds your bot token.

| Setting | Required? | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | **Required** | The bot token from Section 6. Without this, WASH cannot start. |
| `DISCORD_GUILD_ID` | Optional | Syncs slash commands to one server instantly instead of waiting up to an hour for global sync. Strongly recommended while you're setting up and testing. |
| `WASH_CREW_ROLE_ID` | Optional, but see note | The Discord role ID authorized to run administrative commands. |
| `WATCH_PARTY_MEMBER_ROLE_ID` | Optional, but see note | The Discord role ID authorized to use participant commands (`/add`, `/list`, `/stats`, etc.). WASH Crew members automatically have these permissions too. |
| `DEFAULT_VOTE_NOMINEE_COUNT` | Optional | Default number of nominees `/vote start` selects (2-10). Defaults to 3. |
| `OMDB_API_KEY` | Optional | Enables resolving pasted IMDb links into a title, runtime, genres, and poster. See Section 8. |

> [!IMPORTANT]
> `WASH_CREW_ROLE_ID` and `WATCH_PARTY_MEMBER_ROLE_ID` can both be set here directly, **or** configured later through the guided `/setup` wizard (Section 11) once WASH is already running in your server -- the wizard writes them into WASH's own persisted server configuration rather than `.env`. Either path works; most users find it easier to leave these blank in `.env` and let `/setup` walk them through role selection interactively. Until at least one of these two roles is configured (by either method), every restricted command fails closed -- nobody, including server administrators, can use them. This is deliberate, not a bug.

You do not need a Discord role's numeric ID handy to use `/setup` -- the wizard lets you pick roles directly from your server. You only need the raw numeric ID if you're setting `WASH_CREW_ROLE_ID`/`WATCH_PARTY_MEMBER_ROLE_ID` in `.env` yourself; get it in Discord via **User Settings -> Advanced -> Developer Mode**, then right-click the role and choose **Copy Role ID**.

## 8. OMDb API Configuration (Optional)

WASH can accept a plain title for `/add` (e.g. "The Matrix") with no external service involved at all. OMDb is only needed if you also want members to be able to paste an IMDb link and have WASH automatically fill in the title, runtime, genres, and poster.

1. Go to the [OMDb API key request page](https://www.omdbapi.com/apikey.aspx) and request a free key (the free tier is sufficient for typical Watch Party use).
2. Confirm the key via the email OMDb sends you.
3. Set `OMDB_API_KEY=<your key>` in `.env`.
4. Restart WASH for the change to take effect.

Without a configured key, pasting an IMDb link into `/add` returns a clear message explaining that IMDb lookup isn't configured -- it does not crash, and plain-title suggestions are completely unaffected.

## 9. Invite WASH to Your Server

1. In the [Discord Developer Portal](https://discord.com/developers/applications), open your application, then **OAuth2 -> URL Generator**.
2. Check the scopes and permissions listed in Section 6 -- both `bot` and `applications.commands` must be selected.
3. Copy the generated URL, open it in a browser, choose your server, and authorize.
4. **Verify WASH now appears in your server's member list** (offline until you start it in Section 10). This is the confirmation that the invite actually worked -- if slash commands later show up but WASH was never added as a member, the `bot` scope was likely missing from the invite; see Section 14.

## 10. Start WASH

With the virtual environment active and `.env` configured:

```powershell
python -m watch_party_manager.bot
```

Equivalently, since installation registered a console script:

```powershell
watch-party-manager
```

A successful startup logs command synchronization and shows WASH coming online, and its status in Discord changes to online. Leave this process running -- closing the terminal (or `Ctrl+C`) stops the bot.

If `DISCORD_GUILD_ID` is set, slash commands appear in that server almost immediately. Without it, Discord's global command sync can take up to an hour the first time.

## 11. Run the Setup Wizard

Once WASH is online and you can see its slash commands, run `/setup` as a server administrator (or whichever role you've already designated as WASH Crew). This is a guided, interactive, multi-step flow -- entirely re-runnable at any time to change earlier answers.

**Preparation screen.** The first screen you see (only on a brand-new run, not when resuming a saved draft) is a preparation screen, not one of the numbered steps below. It suggests creating two Discord roles before you continue -- **Watch Party** (members who participate and use WASH's member commands) and **WASH Crew** (administrators who configure and manage WASH) -- and reminds you that setup can be paused with Save & Finish Later and that existing channels or threads can be reused wherever the wizard asks for a destination. You can create the roles now or later; press **Begin Setup** to continue, or **Cancel** to stop without starting a draft.

The wizard then walks through, in order:

1. **WASH Crew role** -- which Discord role has administrative access.
2. **Watch Party role and join mode** -- every member who should participate in watch parties and use WASH's participant commands (`/add`, `/list`, `/stats`, and more) needs this role; until it's configured, only WASH Crew can use them. Also choose how members get it: Self-Service (anyone can join with `/join_watch_party`), Manual (WASH Crew adds members), Approval-Required (requests go to WASH Crew for approval), or Discord-Managed (an existing role you manage outside WASH).
3. **Admin channel** -- where Approval-Required membership requests are posted for WASH Crew, or skip for now.
4. **Home channel** -- where WASH creates its home. Choose **Create New Channel** (recommended; defaults to "Watch Party", editable) or **Use Existing Channel**. Every collection's suggestion thread, and by default the Watched Item Archive thread, is created as a sibling thread under this one channel -- for example:

   ```text
   Watch Party (channel)
   ├── Movie Suggestions
   ├── TV Suggestions
   ├── Special Collection
   └── Watched Item Archive
   ```

5. **Collection** -- **Create New** (recommended, offered first) walks you through a guided prompt: **Movies** or **TV Shows** (creates a collection with a descriptive default thread name -- "Movie Suggestions" or "TV Suggestions" -- with no further naming needed), **Special Collection** or **Custom** (asks for a name first). WASH creates that collection's suggestion thread directly as a sibling under the home channel and saves it immediately as the collection's Suggestion Destination -- `/add` works in it right away, no further configuration needed. **Select Existing** picks one already configured in this server instead. **Import Existing Database** points you at running `/import` separately, since Discord doesn't allow attaching a file from inside this wizard. A collection's suggestion destination can be changed later via `/config`, but never cleared -- every collection always has exactly one.
6. **Watched Item Archive** -- where WASH should archive completed watch items: an existing text channel or thread, a brand-new thread created as a sibling under the home channel (default name "Watched Item Archive", editable -- WASH creates it for you, never nested under a suggestion thread), or skip for now. This archive stores Vote Winners and Retired items together with links back to their suggestion and voting history -- it is not a discussion channel.
7. **Voting defaults** -- candidate count and duration/visibility are entered in a modal (default 3 candidates; duration 1 minute through 30 days, default **1 day**, entered as `10m`, `1h`, `1d`, or `1w` and shown in natural language like "10 minutes", "4 hours", or "3 days"; visibility **Visible** by default, **Blind** remains fully selectable -- explained right on this step: Visible means everyone can see vote totals while voting is active, Blind means results stay hidden until voting closes). Candidate-selection mode is chosen from a dropdown, not typed, each option showing a short explanation of what it does: **Balanced Random** (recommended and the default -- prioritizes suggestions that have appeared in fewer recent votes), **Soft Rotation** (prefers new suggestions, but previously shown ones stay eligible at a lower chance), or **Pure Random** (chooses completely at random, with no preference or exclusion).
8. **Reminder defaults** -- whether a vote-ending reminder is sent, and how long before close (default **1 day**, entered as `10m`, `1h`, `1d`, or `1w`).
9. **Backup defaults** -- **Enable Automatic Backups** (recommended, offered first) then sets the interval and how many backups to retain, or **Disable Automatic Backups** to skip scheduling entirely for now (manual `/backup` always remains available either way, and existing backups are never deleted by disabling this).
10. **Summary** -- review every section (including the chosen candidate-selection mode), then Save, jump back to edit any section, or cancel without saving.

Nothing is applied until you reach the summary and choose to save.

**Back, and Save & Finish Later.** Every step after the first shows a **Back** button that returns to the immediately previous step without losing anything you've already entered -- the first step has no Back button, since there's nothing earlier to return to. Every step also shows a **Save & Finish Later** button: it saves your progress so far, exits the wizard cleanly, and does *not* mark setup as complete. Run `/setup` again at any time to resume exactly where you left off. **Cancel Setup**, by contrast, discards everything entered so far -- use Save & Finish Later if you just want a break.

**Resuming.** If you run `/setup` again while a draft is already saved, WASH shows how many of the 10 steps are done and where you left off, then offers **Continue Setup** (pick up where you stopped), **Review Progress** (jump straight to the summary screen), or **Restart Setup** (discard the saved draft and start over -- never the default choice). Once setup has actually been completed and saved, `/setup` no longer offers to resume or restart at all; it redirects you to `/config` instead.

Every setting the wizard can set (including candidate-selection mode) can also be changed later through `/config`, one section at a time, without re-running the whole wizard -- see Section 12.

## 12. Server Configuration Overview

Everything the setup wizard collects is stored per-server and can be changed afterward without re-running the whole wizard:

- `/config` opens the same settings in a menu, section by section, for quick individual changes -- including a **Rotation Low-Pool Notification** section not covered by the setup wizard: enable/disable, a custom eligible-suggestion threshold (or restore the automatic default of two configured voting rounds), and a destination (Admin Channel, the default, or the Watch Party Home Channel).
- `/database add` and `/database list` create and list additional suggestion databases beyond the one created during setup -- or run `/database manage` for a single guided menu that walks through every action (move, edit, back up, restore, reset, or remove) for a chosen collection. Direct shortcuts (`/database move`, `/database backup`, `/database restore`, `/database reset`, `/database remove`) remain available for experienced administrators. `/database health` reports a collection's rotation eligibility and pool health without changing anything.
- `/watch_party` (WASH Crew only) manages membership directly: list, approve/deny pending requests, manually add or remove members, and search a member's history.

For the complete, current administrative command reference -- including suggestion management, voting operations, backup/restore, and diagnostics -- see [Administration](05-Administration.md). For the full technical shape of what gets persisted (every field the wizard and `/config` can set), see [`guild_configuration_spec.md`](guild_configuration_spec.md).

## 13. Installation Verification Checklist

Work through this list after your first startup and `/setup` run. Everything should succeed before you consider the installation complete.

- [ ] WASH shows as **online** in your server's member list.
- [ ] `/help` responds (available to everyone).
- [ ] `/about` responds and shows a version number.
- [ ] `/setup` completes without errors and reaches the summary screen.
- [ ] `/config` opens and shows the settings you just configured.
- [ ] A Watch Party member (or WASH Crew, which always qualifies) can run `/add` with a plain title and see it confirmed.
- [ ] `/list` shows the suggestion you just added.
- [ ] WASH Crew can run `/vote start` and see an interactive voting post with buttons.
- [ ] `/stats` responds ephemerally (visible only to you).
- [ ] As WASH Crew, `/about` also shows Health, Configuration, and Runtime sections with no reported errors.

If a step fails, check Section 14 before assuming something is broken.

## 14. Troubleshooting

| Symptom | Likely Cause | Fix |
| --- | --- | --- |
| WASH never comes online | `DISCORD_TOKEN` is missing, wrong, or was reset in the Developer Portal after you copied it. | Reset the token in the Developer Portal, update `.env`, restart WASH. |
| WASH logs a configuration error and exits immediately | A malformed value in `.env` (e.g. `WASH_CREW_ROLE_ID` isn't a plain number). | Fix or remove the offending line; unset optional values are fine left blank or commented out. |
| Slash commands don't appear in Discord | Global sync can take up to an hour on first install; or the bot wasn't invited with the `applications.commands` scope. | Set `DISCORD_GUILD_ID` for instant sync during setup, or wait; re-invite with the correct scope if commands never appear. |
| Slash commands appear and respond, but WASH does not appear in the server member list | The `bot` scope is probably missing from Guild Install -- the server only granted `applications.commands`, so Discord routes command interactions without WASH ever having joined as a member. This breaks anything that depends on WASH being an actual member, including `/setup`'s first-run owner check. | Re-invite WASH with both `bot` and `applications.commands` selected under Guild Install (Section 6/9), and confirm WASH now shows up in the member list before running `/setup` again. |
| "You need the WASH Crew role to use this command" / commands fail closed for everyone | Neither `WASH_CREW_ROLE_ID` nor `/setup`'s WASH Crew step has been configured yet. | Run `/setup`, or set `WASH_CREW_ROLE_ID` in `.env` and restart. |
| "You need the Watch Party member role..." | `WATCH_PARTY_MEMBER_ROLE_ID` isn't configured and you're not WASH Crew. | Run `/setup`'s Watch Party role step, or join via `/join_watch_party` if Self-Service mode is enabled. |
| Pasting an IMDb link into `/add` says lookup isn't configured | `OMDB_API_KEY` is unset. | Follow Section 8, or continue using plain titles -- this is optional. |
| `/stats server`'s member count looks too low | The Server Members Intent isn't enabled (see Section 6's note). | Enable it in the Developer Portal if you want that one figure to be accurate; otherwise it's safe to ignore. |
| WASH can't post in a channel | Missing View Channel/Send Messages/Embed Links permission in that specific channel (server-wide invite permissions don't override channel-level overwrites). | Check that channel's permission overwrites for WASH's role. |
| The Setup Wizard's "Create New Channel" option fails with a permission message | WASH wasn't invited with Manage Channels (see Section 6). | Grant WASH the Manage Channels permission and try again, or choose **Use Existing Channel** in that step instead. |
| Changes made via `/restore`, `/database restore`, `/database reset`, `/factory_reset`, or `/import` don't seem to take effect | Several repositories cache their data in memory at startup. | Restart WASH after any of these commands -- see [Administration](05-Administration.md)'s "Backup & Recovery" section for the full explanation. |
| `python -m pip install -e .` fails | Python version below 3.12, or the virtual environment isn't active. | Confirm `python --version` reports 3.12+ and your prompt shows `(.venv)`. |

## 15. Where to Go Next

- [Administration](05-Administration.md) -- the complete command reference and day-to-day administration behavior.
- [Expanded Help](08-Expanded-Help.md) -- the same reference `/help` links to in Discord.
- [Glossary](98-Glossary.md) -- terminology used throughout WASH and its documentation.
- [Developer Guide](06-Developer-Guide.md) -- if you intend to modify WASH's code or run its test suite.
