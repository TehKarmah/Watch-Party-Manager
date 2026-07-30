# Watch Party Manager

## Release Validation Checklist

| Property | Value |
| --- | --- |
| Document | Release Validation Checklist |
| File | `11-Release-Validation-Checklist.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | July 2026 |
| Authors | TehKarmah & ChatGPT |

## Purpose

This checklist is the official acceptance checklist for the Watch Party Manager v1.0 release. It is written for a tester with no prior familiarity with the codebase: every step names the exact command, option, or button to use. Complete it against a real Discord server and a real (test) bot application -- this is manual, human verification, not a substitute for the automated test suite.

## How to Use This Checklist

- Work through the sections in order. Later sections (Voting, Statistics, Backup & Restore) assume earlier ones (Environment Preparation, First-Time Setup) already passed.
- Each test lists an **Objective**, **Preconditions**, numbered **Steps**, an **Expected Result**, a **Result** checkbox, and (where useful) a **Notes** line for anything observed that doesn't fit a simple pass/fail.
- Check exactly one of Pass/Fail per test. A test you could not run at all (missing prerequisite, environment issue) should be marked **Fail** with the reason in Notes -- do not leave it blank.
- "WASH Crew" and "Watch Party member" below refer to whichever Discord roles your test server has configured for those purposes (see Section 2).
- Where a step says "confirm the exact wording," minor phrasing drift is not itself a failure -- flag it in Notes as a documentation/consistency item rather than blocking the release on it, unless the message is actually misleading or wrong.
- Run `python -m unittest discover -s tests -v` (baseline: 3216 tests, 0 failures) before starting manual validation, and again before final sign-off. This checklist verifies real-world behavior the automated suite cannot (Discord UI rendering, actual message delivery, a real bot process restarting) -- it does not replace the automated suite.

---

## 1. Environment Preparation

### 1.1 Fresh clone

- **Objective:** Confirm the repository is installable from a clean checkout with no leftover local state.
- **Preconditions:** A machine with no existing Watch Party Manager checkout or `data/` directory.
- **Steps:**
  1. `git clone` the repository to a new directory.
  2. Confirm no `data/` directory exists yet (it's created on first run).
- **Expected Result:** Clone completes without error; repository contains `pyproject.toml`, `src/watch_party_manager/`, `tests/`, `docs/`, `env.example`.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.2 Python installation

- **Objective:** Confirm the required Python version is available.
- **Preconditions:** None.
- **Steps:**
  1. Run `python --version` (or `py -3.12 --version` on Windows).
- **Expected Result:** Python 3.12 or later is reported.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.3 Virtual environment

- **Objective:** Confirm a virtual environment can be created and activated.
- **Preconditions:** Section 1.2 passed.
- **Steps:**
  1. `py -3.12 -m venv .venv` (or `python3.12 -m venv .venv` on macOS/Linux).
  2. Activate it (`.\.venv\Scripts\Activate.ps1` on Windows PowerShell, `source .venv/bin/activate` elsewhere).
- **Expected Result:** The environment activates with no error; the shell prompt reflects the active venv.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.4 Dependency installation

- **Objective:** Confirm all runtime and development dependencies install cleanly.
- **Preconditions:** Section 1.3 passed; venv is active.
- **Steps:**
  1. Run `python -m pip install -e .` (or the project's documented equivalent).
- **Expected Result:** Installation completes with no errors; `discord.py`, `python-dotenv`, and other declared dependencies are installed.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.5 `.env` configuration

- **Objective:** Confirm the example environment file documents every setting needed to start the bot.
- **Preconditions:** Section 1.4 passed.
- **Steps:**
  1. Copy `env.example` to `.env`.
  2. Review each documented variable against [Administration](05-Administration.md) Section 2 and the [Installation Guide](09-Installation-Guide.md).
- **Expected Result:** `env.example` lists `DISCORD_TOKEN`, `DISCORD_GUILD_ID`, `WASH_CREW_ROLE_ID`, `WATCH_PARTY_MEMBER_ROLE_ID`, `DEFAULT_VOTE_NOMINEE_COUNT`, and `OMDB_API_KEY`, matching the documented purpose of each.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.6 Discord application and bot token

- **Objective:** Confirm a Discord application/bot can be created and its token obtained, following the Installation Guide alone.
- **Preconditions:** A Discord account with permission to create applications.
- **Steps:**
  1. Follow the [Installation Guide](09-Installation-Guide.md)'s Discord application steps exactly, without outside help.
  2. Copy the bot token into `.env` as `DISCORD_TOKEN`.
- **Expected Result:** A bot application and token exist; the guide's instructions are sufficient on their own (no missing step, no stale screenshot reference).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.7 OMDb key (optional feature)

- **Objective:** Confirm IMDb metadata resolution can be enabled, and that WASH still works without it.
- **Preconditions:** Section 1.6 passed.
- **Steps:**
  1. Obtain a free OMDb API key (see Installation Guide).
  2. Set `OMDB_API_KEY` in `.env`.
- **Expected Result:** The key is accepted; later `/add` tests (Section 4) that use an IMDb link resolve title/runtime/genre/poster metadata once the bot is running. Leaving this unset does not prevent startup or plain-title suggestions.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.8 Invite the bot to a test server

- **Objective:** Confirm the documented OAuth2 invite steps produce a bot with the correct permissions in a real server.
- **Preconditions:** Section 1.6 passed; a Discord server you administer, used only for this validation pass.
- **Steps:**
  1. Follow the Installation Guide's invite-link/permission steps.
  2. Confirm the bot appears in the server's member list (offline is expected until first startup).
- **Expected Result:** The bot joins the server with the documented permissions (send messages, embed links, manage messages where applicable, etc.) and no permission errors are reported by Discord at invite time.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.9 First startup

- **Objective:** Confirm the bot starts cleanly against a brand-new `data/` directory.
- **Preconditions:** Sections 1.1-1.8 passed.
- **Steps:**
  1. Run `python -m watch_party_manager.bot`.
  2. Observe console/log output.
  3. Confirm the bot shows as online in the test server.
- **Expected Result:** Startup logs show a successful Discord connection, command sync, and no unhandled exceptions; a `data/` directory is created; the bot's presence in Discord shows online.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.10 `/about` and `/help` respond before setup

- **Objective:** Confirm the two commands available to everyone work immediately, before any server configuration exists.
- **Preconditions:** Section 1.9 passed; `/setup` has not been run yet.
- **Steps:**
  1. Run `/about` as any server member.
  2. Run `/help` as any server member.
- **Expected Result:** `/about` shows WASH's identity and documentation links (no Health/Configuration/Runtime sections yet, since the member isn't WASH Crew). `/help` shows only the commands available to an unconfigured member (`/help`, `/about`, `/join_watch_party`).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.11 Command tree matches the documented structure (Command Structure Cleanup)

- **Objective:** Confirm Discord's own slash-command autocomplete shows exactly the documented command tree -- grouped commands registered correctly, obsolete underscore commands gone, no duplicate command roots, and no Discord naming-rule violations.
- **Preconditions:** Section 1.9 passed (bot online, commands synced).
- **Steps:**
  1. Type `/database` in Discord's command box; confirm autocomplete offers exactly `add`, `manage`, `list`, `move`, `backup`, `restore`, `remove`, `reset` as subcommands, and that typing `/database_add`, `/database_backup`, `/database_restore`, `/database_reset`, `/database_list`, or `/database_remove` (the old top-level names) finds nothing.
  2. Type `/vote`; confirm autocomplete offers exactly `start`, `status`, `edit`, and that `/start_vote`, `/vote_status`, `/edit_vote`, and the pre-release `/voting` group no longer exist.
  3. Type `/watch-party`; confirm autocomplete offers exactly `schedule`, `status`, `reschedule`, `cancel`, and that `/schedule_watch_party`, `/reschedule_watch_party`, `/cancel_watch_party`, `/watch_party_status` no longer exist.
  4. Type `/watch_party` (underscore); confirm it still exists, separately from `/watch-party` (hyphen), and still offers `members`, `pending`, `approved`, `denied`, `add`, `remove`, `search` -- membership administration is unaffected by this cleanup.
  5. Confirm every other command named in [Command Reference](10-Command-Reference.md) (e.g. `/about`, `/add`, `/backup`, `/config`, `/help`, `/list`, `/restore`, `/setup`, `/stats`, `/remove`, `/edit_suggestion`, `/reject`, `/unreject`, `/repair_suggestions`, `/factory_reset`, `/import`, `/join_watch_party`) still appears as a plain top-level command, unchanged.
- **Expected Result:** The live Discord command tree matches [Command Reference](10-Command-Reference.md) exactly -- no obsolete command remains reachable under its old name, no command was accidentally duplicated under both an old and new name, and `/watch_party`/`/watch-party` coexist as the two distinct, valid command names documented there.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 2. First-Time Setup

### 2.1 `/setup` starts the wizard

- **Objective:** Confirm the guided setup wizard launches for a server administrator on an unconfigured server.
- **Preconditions:** Section 1.9 passed; no prior `/setup` run on this server.
- **Steps:**
  1. As a server administrator, run `/setup`.
- **Expected Result:** The wizard opens on its first step (WASH Crew role selection), ephemeral, with no enabled **Back** button (there's nothing earlier to return to).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.2 Role configuration

- **Objective:** Confirm the WASH Crew role and Watch Party role/join-mode steps save correctly.
- **Preconditions:** Test 2.1 passed.
- **Steps:**
  1. Select a WASH Crew role.
  2. Select a Watch Party role and a join mode (Self-Service, Manual, Approval-Required, or Discord-Managed).
- **Expected Result:** Both selections advance the wizard; the chosen role names and join mode later appear correctly on the Review step (Test 2.10).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.3 Back navigation

- **Objective:** Confirm the wizard's Back button returns to the previous step without losing later answers.
- **Preconditions:** At least two steps completed (Test 2.2).
- **Steps:**
  1. On the current step, click **Back**.
  2. Confirm the previous step's previously-selected value is still shown/selected.
  3. Click forward again (re-answer or re-confirm) and continue.
- **Expected Result:** Back moves exactly one step back; the earlier answer is preserved; re-advancing does not duplicate or corrupt state.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.3b Validation failure at Save preserves state and returns to Review

- **Objective:** Confirm that when Save (on Review) fails validation -- e.g. WASH can no longer post in the previously selected Admin Channel because a permission was revoked after it was chosen -- fixing just that one value returns straight to Review with every other already-answered value intact, rather than forcing the whole rest of the wizard to be re-walked.
- **Preconditions:** All steps completed once, reaching Review, with a resource (e.g. the Admin Channel) that will fail validation (its permissions changed, or it was deleted, after being selected).
- **Steps:**
  1. From Review, click **Save**.
  2. Confirm WASH redirects to the specific failing step (e.g. "Step 3 of 10: Admin Channel") with a clear, actionable error -- not a generic failure, and not silently discarding the review state.
  3. Fix the value there (pick a channel WASH can actually use).
  4. Confirm the result.
- **Expected Result:** Step 4 returns directly to Review -- not Home Channel or any other step in between -- and every other section (roles, join mode, collection, voting defaults, reminders, backups) still shows exactly what was entered before Save was first clicked. Save now completes successfully without re-entering anything. The error names the channel directly (e.g. "WASH cannot send messages in #admin") and says exactly which permissions to grant (View Channel, Send Messages), suggests choosing a different Admin channel, and suggests creating a new one -- never a generic "no permission" message, never raw exception text, and never suggesting the Administrator permission. If the channel is private, the message also reminds that WASH's role must be explicitly added to it, and that WASH's role may need to sit higher in the role hierarchy if it also manages roles elsewhere.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.3c Private Admin Channel visibility guidance

- **Objective:** Confirm the Admin Channel step explains why a private channel might not appear in the picker, before the administrator gets confused wondering where it went.
- **Preconditions:** A private channel exists that WASH has not yet been granted access to.
- **Steps:**
  1. Reach the Admin Channel step; confirm the body text includes a note (e.g. "🔒 Private channels only appear...") explaining that WASH must be granted **View Channel** and **Send Messages** on a private channel before it appears in the picker, and that reopening the step (or running `/setup` again) refreshes the list.
  2. Confirm the note also mentions that if WASH assigns server roles, its own role must sit above those roles in the role hierarchy.
  3. Grant WASH those permissions on the private channel from Discord's own channel settings, then reopen this step; confirm the channel now appears in the destination picker.
- **Expected Result:** The guidance is visible without requiring any interaction, names the exact two permissions needed, mentions role hierarchy, and never suggests granting Administrator. The channel appears in the picker once access is actually granted, without needing to restart the wizard.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.4 Save & Finish Later, then resume

- **Objective:** Confirm partial progress can be saved and resumed without loss.
- **Preconditions:** At least two steps completed.
- **Steps:**
  1. On any step, click **Save & Finish Later**.
  2. Confirm the confirmation message explains how to resume.
  3. Run `/setup` again.
  4. Choose **Continue Setup**.
- **Expected Result:** The save confirmation is clear and non-destructive (setup is not marked complete). Running `/setup` again shows a resume prompt naming how many of 10 steps are complete and the current step; **Continue Setup** returns to exactly where you left off with all prior answers intact.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.4b Home Channel

- **Objective:** Confirm WASH's home channel can be created fresh or pointed at an existing channel, and that every collection's suggestion thread (and, by default, the Watched Item Archive thread) is created inside it.
- **Preconditions:** Test 2.4 passed.
- **Steps:**
  1. Reach the Home Channel step; confirm it offers **Create New Channel (Recommended)** and **Use Existing Channel**.
  2. Choose **Create New Channel**; confirm the name prompt defaults to "Watch Party" and can be changed; confirm the channel is actually created in the server.
  3. Separately, repeat choosing **Use Existing Channel**; confirm it shows a channel picker and saves the selected channel.
- **Expected Result:** Either path advances the wizard with a home channel saved; this channel becomes the parent for every thread created in later steps.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.5 Collection (suggestion destination)

- **Objective:** Confirm a collection (internally an ordinary `SuggestionDatabase` record) is created with its own suggestion thread automatically created as a sibling under WASH's home channel, and that thread is immediately saved as that collection's Suggestion Destination -- no separate destination choice, and no additional configuration needed before `/add` works in it.
- **Preconditions:** Test 2.4b passed, reached the Collection step.
- **Steps:**
  0. Before choosing anything, confirm the buttons appear in this order: **Create New** (primary/highlighted style), **Select Existing** (secondary style), **Back**, **Save & Finish Later**, then **Cancel Setup** (danger/red style, visually separated from the rest).
  1. Choose **Create New**; confirm a "What type of collection would you like to create?" screen appears with **Movies (Recommended)**, **TV Shows**, **Special Collection**, **Custom**, and **Import Existing Database**.
  2. Choose **Movies**; confirm no name prompt and no destination-choice screen appear -- a thread named "Movie Suggestions" (the descriptive default, not the bare type name "Movies") is created directly under the home channel and the wizard advances. Repeat with **TV Shows** and confirm its default thread name is "TV Suggestions".
  3. In the server, open the new "Movie Suggestions" thread and run `/add` with any title; confirm the public suggestion post is created immediately in that thread, with no "no suggestion channel is configured" error.
  4. Separately, repeat with **Special Collection** or **Custom**; confirm a name prompt appears first (still fully custom, unaffected by the Movies/TV Shows default naming), then the thread is created using that name.
  5. Separately, click **Import Existing Database**; confirm WASH explains that `/import` must be run as its own command (Discord does not allow attaching a file from inside this wizard) and offers a way back to the type-choice screen.
- **Expected Result:** The Collection step's buttons appear in the order and styles described in step 0 -- **Create New** is the recommended/default (primary) action, **Select Existing** is secondary, and **Cancel Setup** is styled as danger. Movies/TV Shows create a collection using their descriptive default thread name ("Movie Suggestions"/"TV Suggestions") with no name prompt; Special Collection/Custom collect a name first; every creation path ends with the collection's suggestion thread created as a sibling under the home channel and immediately persisted as that collection's Suggestion Destination (and, internally, all remain ordinary `SuggestionDatabase` records). `/add` works in the new thread right away. Import Existing never fakes an in-wizard upload.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.5c Nested Collection screens expose Back and Save & Finish Later

- **Objective:** Confirm the "what type of collection" screen and the "select an existing collection" screen (both nested under the Collection step) offer **Back** and **Save & Finish Later**, not just **Cancel Setup** -- live testing found these two buttons missing, leaving a full destructive cancel as the only way to exit safely.
- **Preconditions:** Test 2.5 passed.
- **Steps:**
  1. From the Collection step, click **Create New** to reach the "what type" screen; confirm **Back**, **Save & Finish Later**, and **Cancel Setup** all appear alongside the five type buttons.
  2. Click **Back**; confirm it returns to the Collection step (Create New / Select Existing), not an earlier wizard step, and every previously entered value (roles, channels) is still intact.
  3. Return to the "what type" screen and click **Save & Finish Later**; confirm setup exits without creating a collection and without marking setup complete, and `/setup` later resumes at the Collection step.
  4. Repeat steps 1-3 for the "select an existing collection" screen (reached via **Select Existing**, with at least one collection already present).
- **Expected Result:** Both nested screens expose the same Back/Save & Finish Later/Cancel Setup trio as every top-level step; Back returns to the Collection step's own choice screen; Save & Finish Later never creates a collection or marks setup complete.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.5b Duplicate destination is rejected (Conflict Prevention)

- **Objective:** Confirm a channel or thread already used by one database cannot be assigned to a second one.
- **Preconditions:** At least one suggestion database already exists, tied to a known channel.
- **Steps:**
  1. Create a second database (via the Setup Wizard's Create New flow, or `/database add`) and attempt to select the same channel already used by the first database.
- **Expected Result:** WASH shows a clear error naming the conflict and does not create the second database (or does not save the destination change); routing never becomes ambiguous.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.6 Watched Item Archive

- **Objective:** Confirm the Watched Item Archive step accepts an existing channel/thread, can create a new thread as a sibling under the home channel, or can be skipped, and that its wording clearly describes archiving completed watch items rather than general discussion.
- **Preconditions:** Test 2.5 passed.
- **Steps:**
  1. Reach the step; confirm the body text explains WASH archives completed watch items (Vote Winners and Retired items) together with links back to their suggestion and voting history -- not that it's a discussion or general history channel.
  2. Select an existing destination channel or thread, or click **Skip for Now**.
  3. Separately, repeat and instead choose **Create New Thread**; confirm the name prompt defaults to "Watched Item Archive" and the new thread is created as a sibling under the home channel (never nested under a suggestion thread).
- **Expected Result:** Every choice advances the wizard and is reflected correctly on the Review step ("Watched Item Archive: Configured/Skipped/Incomplete").
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.6c Thread discovery after revisiting Step 6

- **Objective:** Confirm the Watched Item Archive selector always shows every currently active, accessible thread -- including one created earlier in the very same setup session -- and never a stale list; live testing found a collection's own suggestion thread (created in Step 5) missing from this step's picker.
- **Preconditions:** At least one collection created (Test 2.5), so its suggestion thread already exists under the home channel.
- **Steps:**
  1. Reach Step 6; open the destination picker and confirm the Step 5 collection's suggestion thread appears as an option, labeled with its parent channel for context (e.g. "watch-party › movie-suggestions").
  2. Select it, then click **Back** and return to Step 6 again; confirm the same thread is still listed and now shows as the pre-selected value.
  3. Separately, archive or lock that thread in Discord directly, then reopen Step 6; confirm the archived/locked thread no longer appears as a selectable option.
  4. Create a brand-new thread under the home channel from outside the wizard (e.g. via `/database add`), then reopen Step 6 without restarting the bot; confirm that thread also appears immediately.
- **Expected Result:** The destination list is rebuilt fresh every time this step renders -- never cached -- always includes active, accessible threads with parent-channel context, and excludes archived/locked/inaccessible ones. The previously saved destination is always shown pre-selected, never silently cleared.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.6b Server-wide default Watched Item Archive (`/config`)

- **Objective:** Confirm `/config`'s Watched Item Archive (Default) section sets a server-wide fallback, and that a collection's own override takes precedence when both are set.
- **Preconditions:** Setup completed; at least one collection exists.
- **Steps:**
  1. Run `/config` -> **Watched Item Archive (Default)**; set it to a channel, or clear it.
  2. Run `/config` -> **Manage Collections** -> a collection -> its Watched Item Archive; confirm the screen shows both "This collection's own override" and "Currently effective" values.
  3. With no per-collection override set, confirm "Currently effective" matches the server default from step 1.
  4. Set a per-collection override to a different channel; confirm "Currently effective" now shows the override, not the server default.
  5. Clear the per-collection override; confirm "Currently effective" falls back to the server default again.
- **Expected Result:** The server-wide default may be unset (None) or shared across any number of collections -- unlike a suggestion destination, Watched Item Archive destinations are never checked for conflicts. A collection's own override, when set, always wins over the server default.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.7 Voting Defaults: candidate count, duration, visibility

- **Objective:** Confirm the Voting Defaults modal accepts and validates all four fields, and that the step explains what Visible/Blind actually mean.
- **Preconditions:** Reached the Voting Defaults step.
- **Steps:**
  0. Before opening the modal, confirm the step's body text explains: "Visible -- everyone can see vote totals while voting is active. Blind -- results stay hidden until voting closes."
  1. Open the modal (**Set Voting Defaults**); confirm the duration field's label reads "Default vote duration (1m-30d; 10m,1h,7d)" (Duration UX Standard -- range and examples in the label itself, since this field always has a value and its placeholder would never show) and the field is pre-filled `1d` (not "1 day") on a brand-new server.
  2. Enter a candidate count (try an in-range value, e.g. `4`).
  3. Enter a duration using WASH's shared duration syntax (try `4h`, then separately `3d`, then `3 days` -- confirm all are accepted and mean what you'd expect). Confirm a bare number with no unit (e.g. `3`) is rejected -- an explicit unit is always required. Separately, confirm `1m` (the new one-minute minimum) is accepted.
  4. Set visibility to `visible` or `blind`.
  5. Submit, then reopen the modal and confirm the just-saved duration redisplays in the same compact form (e.g. `4h`, not "4 hours").
- **Expected Result:** Values in range are accepted; the modal's duration field is pre-filled `1d` on a brand-new server and always redisplays previously saved durations in compact form. An out-of-range duration (e.g. `31d` / `0m`) is rejected with a clear, specific error naming the actual 1 minute-30 day range; a unitless value is rejected with a clear syntax error that shows only the `10m`/`1h`/`7d` examples (never `1w` or a duplicate day example).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.8 Candidate selection mode and its help text

- **Objective:** Confirm all three candidate-selection modes are selectable from the dropdown, each with a plain-language description, and that **Balanced Random** is the pre-filled default.
- **Preconditions:** Reached the Voting Defaults step (before pressing **Set Voting Defaults**).
- **Steps:**
  1. Open the candidate-selection dropdown; confirm it lists exactly **Balanced Random (Recommended)**, **Soft Rotation**, and **Pure Random**, with **Balanced Random (Recommended)** preselected.
  2. Confirm each option shows a short description beneath its label: Balanced Random -- "Prioritizes suggestions that have appeared in fewer recent votes, giving every one a fair chance."; Soft Rotation -- "Prefers new suggestions; previously shown ones stay eligible, just at a lower chance."; Pure Random -- "Chooses completely at random from eligible suggestions, with no preference or exclusion."
  3. Select each mode in turn, press **Set Voting Defaults**, and confirm the modal opens (candidate count/duration/visibility only -- candidate selection is not one of its fields).
- **Expected Result:** All three modes are selectable from the dropdown with the exact descriptions above; the chosen mode is saved correctly regardless of which one was picked.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.9 Default voting visibility is Visible

- **Objective:** Confirm the documented v1.0 default (Visible, not Blind) is what a brand-new server actually gets.
- **Preconditions:** A brand-new server that has never had Voting Defaults explicitly submitted.
- **Steps:**
  1. Reach the Voting Defaults step without yet submitting the modal.
  2. Open the modal and read the visibility field's pre-filled value.
- **Expected Result:** The field is pre-filled `visible`. Blind remains fully selectable by entering `blind`.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.10 Reminder Defaults

- **Objective:** Confirm the Reminder Defaults step's Enable/Disable choice works correctly (Fixed-Option UX Audit: "enabled?" is a button choice, not a free-text yes/no field) and its duration examples are consistent with every other duration field.
- **Preconditions:** Test 2.7 passed.
- **Steps:**
  1. Reach the Reminder Defaults step; confirm it shows **Enable Vote-Ending Reminder (Recommended)** and **Disable Vote-Ending Reminder** buttons, not a modal.
  2. Press **Disable Vote-Ending Reminder**; confirm no modal opens and the wizard advances straight to Backup Defaults.
  3. Go back and press **Enable Vote-Ending Reminder (Recommended)** instead; confirm a modal opens with exactly one field, labeled "Reminder before close (1m-30d; 10m,1h,7d)", pre-filled with a compact value (e.g. `1d`, not "1 day").
  4. Submit using WASH's shared duration syntax -- try minute precision (e.g. `10m`, `30m`, and the new one-minute minimum `1m`) as well as hours/days (e.g. `1h`, `7d`), and confirm an out-of-range or malformed value is rejected with a clear error while keeping the Enable choice (no need to re-pick Enable/Disable).
- **Expected Result:** Disable saves immediately with no modal; Enable opens a single-field modal that accepts valid values (minute precision, not just whole hours) and rejects invalid ones with a clear error; the field's label and pre-filled value are both in compact form.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.10b Backup Defaults: Enable/Disable Automatic Backups

- **Objective:** Confirm the Backup step lets an administrator enable (with interval/retention configuration) or disable automatic backups, and that Disable skips interval/retention configuration for this pass.
- **Preconditions:** Test 2.10 passed.
- **Steps:**
  1. Reach the Backup step; confirm it offers **Enable Automatic Backups (Recommended)** and **Disable Automatic Backups**.
  2. Choose **Enable Automatic Backups**; confirm it opens the interval (days) and retention count fields, and that both accept valid values and reject out-of-range ones with a clear error.
  3. Reach the Backup step again on a separate run (or click **Back** then re-choose), and this time choose **Disable Automatic Backups**; confirm the wizard skips interval/retention configuration entirely and advances straight to the next step.
- **Expected Result:** Enable opens the existing interval/retention configuration flow; Disable skips it and continues; **Back**, **Save & Finish Later**, and **Cancel Setup** all remain available on this step either way.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.11 Review and completion summary

- **Objective:** Confirm the final Review step accurately reflects every prior answer, and that Back still works from it.
- **Preconditions:** All prior steps completed.
- **Steps:**
  1. Reach the Review step.
  2. Compare every listed value against what was actually selected in Tests 2.2-2.10.
  3. Click **Back** once, confirm it returns to the immediately preceding step.
  4. Return to Review and click **Save**.
- **Expected Result:** Every section (roles, join mode, admin channel, home channel, suggestion database, Watched Item Archive, voting defaults including candidate selection, reminders, backup) is shown accurately, using natural-language duration (e.g. "4 hours" or "3 days", never "72 hours"). The Automatic Backups line reads "Automatic Backups: Disabled" if you chose Disable in Test 2.10b, or "Automatic Backups: Every N day(s), keep M" (matching the configured interval/retention) if you chose Enable. Save marks setup complete and shows a final completion summary matching the same values, including the same Automatic Backups line.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.11b Final save failure never reports false success

- **Objective:** Confirm that if the final save on the Review step fails for any reason other than a validation issue (e.g. a transient disk/IO error), setup never reports success, the wizard stays open with every value intact, and a retry completes cleanly using the same answers and Discord resources -- no duplicate channel/thread/collection is created.
- **Preconditions:** All prior steps completed, reaching Review.
- **Steps:**
  1. Simulate an unexpected persistence failure on Save (e.g. temporarily make the guild-configurations data file unwritable, or use a debugger/log injection to force an exception in `GuildConfigurationRepository.save`).
  2. Click **Save** and observe the response.
  3. Restore normal write access and click **Save** again.
  4. Run `/config` and check every section against what was entered.
- **Expected Result:** Step 2 never shows the completion summary and never dismisses the wizard (the Review screen redisplays with a clear "could not be saved" warning); the log records which persistence stage failed (guild configuration, or the database's own candidate-selection/watch-destination override) without leaking secrets. Step 3's retry succeeds without re-entering any value or recreating any channel/thread/collection -- the exact same ones created earlier are reused. Step 4 shows every value exactly as entered, and `/config`'s Voting Defaults, Reminder Defaults, and other config-dependent sections all work normally (see Test 2.12b for a guild left without ever completing Step 3 here).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.12 Setup state after completion

- **Objective:** Confirm a completed setup is distinguishable from "not started" and "in progress."
- **Preconditions:** Test 2.11 passed.
- **Steps:**
  1. Run `/setup` again.
- **Expected Result:** WASH reports setup is already complete and redirects to `/config` rather than restarting the wizard.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.12b Recovering a guild left partially configured

- **Objective:** Confirm a guild that has channels, threads, a collection, or suggestions from an earlier, never-finished (or failed) setup attempt can still safely run `/setup` to completion -- without deleting any of that existing data or creating duplicates -- and that config-dependent commands fail safely with an actionable message in the meantime rather than erroring unpredictably.
- **Preconditions:** A guild with at least one collection/suggestion created (e.g. via a `/setup` run stopped or interrupted before reaching Review) but no completed `GuildConfiguration` (`setup_completed` is not True).
- **Steps:**
  1. Run `/config` on this guild.
  2. Run `/setup`; on the Collection step, choose **Select Existing** and pick the collection created earlier.
  3. Complete the remaining steps and Save.
  4. Confirm the collection's suggestions and any created channels/threads are unchanged throughout.
- **Expected Result:** Step 1 shows a friendly "Initial setup hasn't been completed yet. Run `/setup` first." message, not an error. Step 2 offers the existing collection rather than forcing a new one. Steps 3-4 show no data loss and no duplicate collection, channel, or thread at any point -- `/setup` never automatically deletes or resets existing data to "fix" an incomplete configuration.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 3. Configuration

### 3.1 `/config` opens the main menu

- **Objective:** Confirm the always-available configuration menu opens once setup is complete.
- **Preconditions:** Section 2 completed.
- **Steps:**
  1. Run `/config` as WASH Crew.
- **Expected Result:** A menu listing every section (WASH Crew Role, Watch Party Role, Watch Party Join Mode, Admin Channel, Manage Collections, Watched Item Archive (Default), Voting Defaults, Reminder Defaults, Backup Defaults) appears, each showing its current status.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.2 Editing every section persists correctly

- **Objective:** Confirm each `/config` section can be changed and the change survives.
- **Preconditions:** Test 3.1 passed.
- **Steps:**
  1. For each section in the menu, open it, change its value, and submit.
  2. Re-run `/config` and confirm the new value is reflected in the main menu summary.
- **Expected Result:** Every section's edit is saved individually; editing one section never resets or clears another.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.3 Voting Defaults share the same source of truth as Setup

- **Objective:** Confirm `/setup` and `/config` read and write the exact same persisted voting defaults.
- **Preconditions:** Section 2 completed.
- **Steps:**
  1. Note the candidate count, duration, visibility, and candidate-selection mode shown in `/config`'s Voting Defaults section.
  2. Change the duration in `/config` (e.g. to `12h`).
  3. Run `/vote start` -> **Use Defaults** and confirm the new duration is what's actually used (see Test 5.2).
- **Expected Result:** The value changed in `/config` is the value `/vote start` actually applies -- no separate/stale value anywhere.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.3a `/config`'s main menu explains Visible/Blind

- **Objective:** Confirm the Voting Defaults entry in `/config`'s main menu dropdown explains visibility, since its modal opens directly with no intro screen to show the fuller explanation on.
- **Preconditions:** Setup completed.
- **Steps:**
  1. Run `/config`; open the section dropdown without selecting anything yet.
  2. Find the **Voting Defaults** entry and read its description text.
- **Expected Result:** The description reads "Visible: totals shown live. Blind: hidden until voting closes." (or equivalent); no other menu entry shows this description.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.3b `/config` Backup Defaults: enable, disable, and re-enable

- **Objective:** Confirm `/config`'s Backup Defaults section can enable, disable, and re-enable automatic backups, and that the summary and actual scheduling behavior both reflect the current setting.
- **Preconditions:** Section 2 completed (automatic backups enabled or disabled, either way, from Test 2.10b).
- **Steps:**
  1. Run `/config` -> **Backup Defaults**; confirm the summary line reads either "Automatic Backups: Disabled" or "Automatic Backups: Every N day(s), keep M" matching the current setting.
  2. Choose **Disable Automatic Backups** (if not already disabled); confirm the summary updates to "Automatic Backups: Disabled" and that any existing backup files in `data/backups/` are still present afterward.
  3. Run `/backup` while automatic backups are disabled; confirm it still succeeds.
  4. Choose **Enable Automatic Backups** again, setting an interval and retention count; confirm the summary updates to "Automatic Backups: Every N day(s), keep M" matching what you entered.
- **Expected Result:** Disabling never deletes existing backups and never blocks manual `/backup`; enabling and disabling both take effect immediately and are reflected accurately in the summary line.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4 Database management: create, list, remove

- **Objective:** Confirm suggestion database administration works outside the wizard, using `/database add`'s modernized type-then-destination flow, and that collections live in threads only.
- **Preconditions:** WASH Crew role configured; at least one collection (e.g. "Movie Suggestions") already exists; a Home Channel is configured.
- **Steps:**
  1. Run `/database add`; confirm the type screen offers every standard type (Movies, TV Shows, Anime, Holiday, Documentaries, Horror) this server doesn't already have a matching collection for, plus **Special Collection** and **Custom** (always present), and does **not** re-offer a type already matching an existing collection (e.g. Movies, from the precondition).
  2. Choose **Custom**; type a new, unused name in the modal.
  3. Confirm the destination screen appears with exactly **Create New Thread (Recommended)**, **Use Current Thread**, and **Use Existing Thread**, in that order -- no channel-based option is offered; choose **Create New Thread**, confirm the suggested name is editable, and submit.
  4. Confirm the new thread is created as a sibling under WASH's configured Home Channel.
  5. Run `/database add` again from inside a thread; confirm **Use Current Thread** is enabled and, when chosen, the collection is created on that same thread with no further prompts.
  6. Run `/database add` again from a plain text channel, including WASH's own Home Channel; confirm **Use Current Thread** appears disabled/greyed out in both cases (never just for the Home Channel specifically).
  7. Run `/database add` again from a location where nothing is usable (e.g. a voice channel's text chat, if reachable) and confirm **Use Current Thread** stays disabled rather than causing an error.
  8. Choose **Use Existing Thread**; confirm the picker lists threads only -- no text channels, including the Home Channel, ever appear.
  9. Run `/database list` and confirm the new database(s) appear.
  10. Run `/database remove`, choose a new database from the picker, and confirm.
- **Expected Result:** The type screen correctly excludes already-used standard types while always offering Special Collection/Custom; the new thread is created as a sibling under WASH's configured Home Channel and the collection is created on it immediately (rejects a duplicate name); **Use Current Thread** is enabled only inside a thread (a text channel, even the Home Channel, never qualifies) and disabled rather than broken otherwise; **Use Existing Thread** never offers a text channel; the list shows name, Active/Inactive status, and item count; removal applies the documented safety/ownership checks and the database no longer appears afterward.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4b `/database move`

- **Objective:** Confirm a collection's suggestion destination can be moved to a different thread without affecting anything else about it, and that WASH's Home Channel can never become a destination.
- **Preconditions:** At least one collection with existing suggestions and at least one completed vote round; a Home Channel is configured.
- **Steps:**
  1. Run `/database move`, choose the collection from the picker.
  2. Confirm the destination screen shows exactly **Create New Thread (Recommended)**, **Use Current Thread**, and **Use Existing Thread**, in that order (same order and options as `/database add`) -- no channel-based option is offered.
  3. Choose **Use Existing Thread** and select a destination thread not already used by another collection.
  4. Confirm the move succeeds; run `/add` in the new destination and confirm the suggestion is created there.
  5. Confirm the collection's earlier suggestion posts (from before the move) are still visible in their original thread, untouched.
  6. Attempt a second move to a destination already used by another collection; confirm it's rejected with a clear "already routed" message and nothing changes.
  7. Repeat step 1-4 choosing **Create New Thread** instead; confirm the new thread is created under WASH's configured Home Channel and the suggested default name can be renamed before creation (renaming here must not change the collection's own name -- only `/database add`'s Create New Thread renames the collection).
  8. Run `/database move` again from inside a thread; confirm **Use Current Thread** is enabled and, when chosen, moves the collection's destination to that same thread with no further prompts.
  9. Run `/database move` again from a plain text channel, including WASH's own Home Channel; confirm **Use Current Thread** stays disabled in both cases.
  10. Attempt to route a collection's suggestion destination to WASH's configured Home Channel (e.g. via `/config` -> Manage Collections -> Suggestion Destination, if reachable, or by any other means available); confirm it's clearly rejected and nothing changes.
- **Expected Result:** Only the collection's suggestion destination changes -- its database ID, suggestions, statuses, vote history, rotation history, and statistics are all unchanged (spot-check via `/stats type:Collection` or `/config` -> Manage Collections before and after); existing Discord suggestion posts are never moved; only suggestions added after the move appear in the new destination; a duplicate destination is rejected cleanly; **Use Current Thread** behaves identically to `/database add`'s version (same enable/disable rule) but never renames the collection; WASH's Home Channel is never accepted as a destination, from any path.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4b2 Context Resolution Audit: commands stop resolving a moved collection's old location

- **Objective:** Confirm the bug fix -- a collection moved into its own thread must no longer resolve from wherever it used to be, and the new destination must resolve immediately, for every context-sensitive command.
- **Preconditions:** Two collections in the same server (so the "exactly one collection" convenience fallback can't mask a failure); collection A moved via Test 3.4b, from its original location to a new thread.
- **Steps:**
  1. From collection A's *original* (pre-move) location, run `/add`, `/list`, `/vote start`, and `/stats type:Collection`; confirm none of them resolve to collection A there anymore (each should show its usual ambiguous/no-match picker or message, since collection B still exists too).
  2. From collection A's *new* thread, run the same four commands; confirm all four resolve to collection A immediately, with no extra step.
  3. Restart the bot, then repeat step 2.
  4. Run `/database list` and `/config` -> Manage Collections; confirm both show collection A's *current* (post-move) channel, not its original one.
  5. Create a brand-new collection C using collection A's *original*, now-freed location as its destination; confirm this succeeds (the old location is genuinely free for reuse, not permanently reserved).
- **Expected Result:** Exactly one location resolves to collection A at any given time -- its current destination -- both before and after a restart; `/database list`/`/config` never show a stale channel; a freed-up former destination can be reused by a different collection.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4c `/database manage`

- **Objective:** Confirm the guided management workflow correctly reuses the existing move, edit, backup, restore, reset, and remove logic without duplicating it.
- **Preconditions:** At least one collection already exists.
- **Steps:**
  1. Run `/database manage`; confirm the same collection picker used elsewhere in `/database` appears.
  2. Choose a collection; confirm a management menu appears offering exactly: **Move Collection**, **Edit Collection**, **Backup Collection**, **Restore Collection**, **Reset Collection**, **Remove Collection**, **Cancel**.
  3. Choose **Move Collection**; confirm it launches the identical destination-choice screen used by `/database move` (same three thread-only options, same order) and completes the move.
  4. Return to `/database manage` and choose **Edit Collection**; confirm it shows the same settings menu `/config` -> Manage Collections shows for that database, and that its **Back** button returns to the `/database manage` management menu (not to `/config`'s picker).
  5. Choose **Backup Collection**; confirm a backup file is produced, matching `/database backup`'s output.
  6. Choose **Restore Collection**; confirm it points the user at running `/database restore` directly (a modal/component interaction cannot carry a file upload, so this cannot be button-driven) rather than silently failing.
  7. Choose **Reset Collection**; confirm the same confirmation and behavior as `/database reset`.
  8. Choose **Remove Collection**; confirm the same behavior as `/database remove` (the collection is deactivated).
  9. Choose **Cancel** at the management menu; confirm the message states no changes were made and nothing is altered.
  10. Confirm `/database move`, `/database backup`, `/database restore`, `/database reset`, and `/database remove` still work directly and unchanged, independent of `/database manage`.
- **Expected Result:** `/database manage` is a thin guided wrapper around the same underlying move/edit/backup/restore/reset/remove logic used by the direct subcommands -- no behavior is duplicated or diverges between the guided and direct paths; the direct subcommands remain fully functional as shortcuts.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4d `/help`'s simplified Collections section

- **Objective:** Confirm `/help`'s Collections section is decluttered to the primary workflow, with the remaining shortcuts still fully documented elsewhere.
- **Preconditions:** WASH Crew role configured.
- **Steps:**
  1. Run `/help` as WASH Crew; find the Collections section.
  2. Confirm it lists exactly `/database add`, `/database list`, and `/database manage` -- not `/database move`, `/database backup`, `/database restore`, `/database reset`, or `/database remove` individually.
  3. Confirm `/database manage`'s summary mentions move, edit, back up, restore, reset, and remove.
  4. Confirm a note appears pointing at additional shortcuts under `/database` and the Command Reference.
  5. Confirm every direct subcommand still runs correctly even though it's no longer individually listed (see 3.4/3.4b/3.4c above).
- **Expected Result:** `/help` reads as a curated, primary-workflow-first summary; nothing about any subcommand's actual behavior changed -- only what `/help` chooses to list.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4e `/database health` (Rotation & Collection Health)

- **Objective:** Confirm `/database health` reports an accurate, reconciled eligibility breakdown for a collection, resolves its collection the same way `/list` does, and never itself changes rotation state.
- **Preconditions:** A Balanced Random (Rotation Pool) collection with a mix of Eligible, Rotation Cooldown, Vote Winner, and Retired suggestions (run a vote round or two, and retire at least one suggestion, if needed); a second collection in the same server.
- **Steps:**
  1. From inside the collection's own thread, run `/database health`; confirm it resolves to that collection automatically with no picker.
  2. Confirm the report shows: Collection Name, Total Watch Items, Active Watch Items, Eligible for Voting, Rotation Cooldown, Vote Winners, Retired, the collection's own current Rotation number and progress (e.g. "Rotation 4 Progress: N of M active items have been presented"), Configured Candidate Count, a **Next Vote** status, and a **Low Pool Status**. Confirm Eligible for Voting and Rotation Cooldown are shown visually nested/indented beneath Active Watch Items.
  3. Confirm the numbers reconcile: Active Watch Items = Eligible for Voting + Rotation Cooldown, and Total = Active Watch Items + Vote Winners + Retired.
  3b. Trigger a second rotation for this collection (e.g. via Test 5.5b) and confirm the shown rotation number increases to 2 for this collection specifically, unaffected by any rotations that have happened for a different collection in the same server.
  4. Click **Switch Collection** and confirm it shows the report for the other collection instead, in place.
  5. Run `/database health` from a channel not tied to either collection; confirm WASH shows a picker instead of guessing.
  6. Immediately after, run `/list status:Eligible for Voting` and separately `/vote start` against the same collection; confirm the eligible count and Rotation Cooldown count reported by `/database health` match exactly what `/list` shows and what `/vote start` actually nominates from.
  7. Run `/database health` again right after step 6; confirm nothing about the collection's rotation state changed as a result of having checked health (no rollover was triggered merely by running this command).
- **Expected Result:** `/database health` never disagrees with `/list`/`/vote start`'s own eligibility count; its collection selection matches `/list`'s (auto-resolve in thread, Switch Collection button, picker when ambiguous); checking health is always side-effect-free -- it never bootstraps or advances a rotation.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.5 Contextual resolution with multiple databases configured

- **Objective:** Confirm behavior is well-defined when more than one suggestion database exists -- WASH resolves the database from context (channel/thread), never from a server-wide "active" pointer.
- **Preconditions:** At least two active suggestion databases in the same server, each tied to a different channel.
- **Steps:**
  1. Run `/add`, `/list`, `/vote start`, or `/stats type:Rotation`/`type:Database` inside one database's configured channel or thread; confirm WASH uses that database automatically, with no prompt.
  2. Run the same command in a channel not tied to either database; confirm WASH asks "Which collection would you like to use?" with a picker listing both instead of guessing.
  3. Run `/config` -> **Manage Collections**; confirm both collections are listed and each is directly, independently editable (destinations and candidate selection) -- neither is reported as "Invalid" for being simultaneously active.
- **Expected Result:** WASH never silently guesses between multiple databases; a matching channel resolves automatically, and an unmatched channel always shows a picker.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 4. Suggestion Management

### 4.1 `/add` by plain title

- **Objective:** Confirm a suggestion can be added by title alone.
- **Preconditions:** A configured suggestion database.
- **Steps:**
  1. Run `/add title:"12 Angry Men"`.
- **Expected Result:** An ephemeral confirmation ("Added...") is shown; the suggestion appears in `/list`.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.2 `/add` by IMDb link

- **Objective:** Confirm IMDb link normalization and metadata resolution.
- **Preconditions:** `OMDB_API_KEY` configured (Test 1.7).
- **Steps:**
  1. Run `/add title:"https://www.imdb.com/title/tt0050083/"` (or paste the link into the `imdb_url` option instead).
- **Expected Result:** The link is normalized to `https://www.imdb.com/title/tt0050083/`; the resolved title/year/runtime/genres/poster appear on the public confirmation post (Test 4.4) when OMDb is configured. A malformed link is rejected with a clear error before anything is saved.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.3 Duplicate detection

- **Objective:** Confirm definite and possible duplicate warnings behave as documented for both regular members and WASH Crew, and that the match line never exposes a raw IMDb URL.
- **Preconditions:** An existing suggestion (e.g. from Test 4.1) with a known release year and a resolved original post reference.
- **Steps:**
  1. As a regular Watch Party member, try to `/add` the exact same title/year again -- confirm it's blocked.
  2. As WASH Crew, try to `/add` the same title with no year -- confirm a possible-duplicate confirmation is offered and reactivation/creation only proceeds after explicit confirmation.
  3. Inspect the matched item's line in the response.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 3's table exactly: active matches always block; archived/watched matches offer WASH Crew reactivation only; possible duplicates require explicit confirmation and never guess. The line reads `Reference #NNNN | <Title> | [Original Suggestion](link) | status: ...` -- a labeled, clickable link to the original Discord message, never a bare IMDb URL (the IMDb preview card already covers that). A legacy match with no recorded original-post reference simply omits that link segment.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.3b Vote Winner duplicate/reactivation view

- **Objective:** Confirm a duplicate match against an existing Vote Winner shows the richer Reference/status/links block (UI Polish: Watch Item Status Presentation), not the old single-line raw-IMDb-URL format.
- **Preconditions:** A suggestion that has won a vote (with a resolved IMDb link and a known original public post); WASH Crew role.
- **Steps:**
  1. As WASH Crew, `/add` the exact same title/year as the Vote Winner.
  2. Inspect the shown block.
- **Expected Result:** The block reads, in order: `Reference #NNNN`, `🏆 Vote Winner`, `Won: <Month D, YYYY>` (only if a win date was recorded -- a legacy Vote Winner with none simply omits this line), a clickable **Original Suggestion** link (only if the original post is known), and a clickable **IMDb** link (only if resolved) -- never a raw, unclickable IMDb URL inline. Confirming reactivates the same existing record (same reference/ID), exactly as before.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.4 Suggestion confirmation post

- **Objective:** Confirm the public confirmation embed posts correctly and matches WASH's visual style.
- **Preconditions:** A suggestion destination is configured (Test 2.5).
- **Steps:**
  1. Run `/add` for a new title.
  2. Inspect the public post in the configured destination channel.
- **Expected Result:** A clean embed (yellow accent, no "TehKarmah"/GitHub branding) shows title, release year, IMDb link (if resolved), and reference number.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.5 Suggestion posting to a public thread destination

- **Objective:** Confirm a public thread works identically to a text channel as a suggestion destination, and that `/add` run *inside* that thread resolves the same database `/config` reports for it.
- **Preconditions:** A suggestion database whose configured post destination is a public thread (change it via `/config` -> Manage Collections -> the collection -> Suggestion Destination, if the database's original channel differs).
- **Steps:**
  1. Confirm `/config` reports the thread as the configured Suggestion Post Destination.
  2. Run `/add` from inside that thread.
  3. Run `/add` from the database's original channel too.
- **Expected Result:** Both locations resolve to the same database with no "no suggestion database configured" error; the confirmation post appears in the thread.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.6 `/list` -- Active Watch Items (default)

- **Objective:** Confirm the default list view mixes Eligible and Rotation Cooldown items with a summary line and per-item status emoji.
- **Preconditions:** A Balanced Random collection with at least one completed vote round, so some suggestions are Eligible and some are on Rotation Cooldown; at least one entry with a known original-post link.
- **Steps:**
  1. Run `/list` (default `status:Active Watch Items`).
- **Expected Result:** Ephemeral by default. A summary line appears before the list, e.g. "🟢 Eligible for Voting: 2" then "🟡 Rotation Cooldown: 6", using the actual counts. Every entry leads with its status emoji (🟢 or 🟡), followed by the title and year exactly once (never doubled, e.g. not `(2004) (2004)`), then `| [Original Suggestion](link)` only when a post link exists. No reference number or IMDb link appears. No embed/link-preview card is shown. Long lists page with Previous/Next.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.6b `/list` -- Eligible for Voting and All Watch Items

- **Objective:** Confirm the Eligible for Voting filter shows only the eligible subset, and All Watch Items shows every status together.
- **Preconditions:** Same collection as Test 4.6, plus at least one Vote Winner and one Retired suggestion.
- **Steps:**
  1. Run `/list status:Eligible for Voting`; confirm it shows only 🟢 entries (no Rotation Cooldown, Vote Winner, or Retired items).
  2. Run `/list status:All Watch Items`; confirm it shows every suggestion regardless of status, each with its own correct emoji.
- **Expected Result:** Eligible for Voting is a strict subset of Active Watch Items (Test 4.6); All Watch Items is the only filter that includes every status at once.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.7 `/list` -- Vote Winners and Retired

- **Objective:** Confirm the other two status filters work, use their new icons, and that a Vote Winner's win date is shown.
- **Preconditions:** At least one retired suggestion (reject one below the retirement threshold in Test 4.10 first, if none exist yet) and at least one completed vote (a suggestion should now be a Vote Winner).
- **Steps:**
  1. Run `/list status:Retired`; confirm each entry leads with 🗄️.
  2. Run `/list status:Vote Winners`; confirm each entry leads with 🏆, followed on the next line by `Won: <Month D, YYYY>` (date only, no time) using the actual date the vote completed.
- **Expected Result:** Retired shows retired/archived items with the 🗄️ icon. Vote Winners shows the suggestion(s) that have won a voting round with the 🏆 icon and their win date; their public confirmation post's Status field should also read "🏆 Vote Winner" followed by the same `Won:` line.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.7a Legacy Vote Winner with no recorded win date

- **Objective:** Confirm a Vote Winner recorded before win dates existed degrades gracefully.
- **Preconditions:** A suggestion manually set to Vote Winner via `/edit_suggestion`'s Change Status action (which does not record a win date), rather than through an actual completed vote.
- **Steps:**
  1. Run `/list status:Vote Winners` and separately `/edit_suggestion` against that suggestion.
- **Expected Result:** Both views show 🏆 Vote Winner with no `Won:` line at all -- never a blank date, a placeholder, or an error.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.7b `/list status:Rotation Cooldown`, thread auto-resolve, and Switch Collection

- **Objective:** Confirm the Rotation Cooldown filter, `/list`'s thread-context auto-resolve, and its Switch Collection button all work, and that Eligible for Voting/Rotation Cooldown never disagree with `/vote start`.
- **Preconditions:** A Balanced Random collection with at least one completed vote round (so some suggestions are on Rotation Cooldown and some remain Eligible); a second collection in the same server.
- **Steps:**
  1. Run `/list status:Rotation Cooldown`; confirm it shows exactly the suggestions presented in the current rotation but not yet a Vote Winner or Retired, each leading with 🟡.
  2. From inside the collection's own thread, run `/list` with no other context; confirm it automatically uses that collection with no picker.
  3. Click **Switch Collection**; confirm it shows the other collection's list in place, without re-running the command.
  4. Run `/list status:Eligible for Voting` and separately start `/vote start`; confirm the eligible count and the actual nominee pool never disagree -- including that if the current rotation can't otherwise supply the configured candidate count, `/list` itself automatically rolls over the rotation the same way `/vote start` would, before showing anything, and shows the same Rotation Refresh Notification text ("All eligible watch items have now been presented. Starting Rotation N.") as a leading line in its own response when it does.
  5. Run `/list status:Vote Winners` (or `Retired`) on a collection that has never had a vote; confirm this never creates rotation state (checking a terminal-status filter alone must never bootstrap a rotation) and never shows the Rotation Refresh Notification.
- **Expected Result:** Rotation Cooldown lists exactly the expected suggestions; thread auto-resolve and Switch Collection both work as described; Eligible for Voting always matches what `/vote start` would actually nominate from, including matching automatic-rollover behavior and its notification; Vote Winners/Retired never trigger a rollover, bootstrap, or notification.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.8 `/list public:true` is WASH Crew only

- **Objective:** Confirm only WASH Crew can post the list publicly.
- **Preconditions:** A regular Watch Party member and a WASH Crew member available.
- **Steps:**
  1. As a regular member, run `/list public:true` -- confirm it's rejected.
  2. As WASH Crew, run `/list public:true` -- confirm it posts visibly in the channel.
- **Expected Result:** Matches exactly.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.9 `/repair_suggestions`

- **Objective:** Confirm the legacy-record repair command runs and reports a summary.
- **Preconditions:** WASH Crew role.
- **Steps:**
  1. Run `/repair_suggestions`.
- **Expected Result:** A summary (scanned/repaired/removed/failed/unchanged counts) is returned. Note: this command repairs legacy IMDb-link titles and known malformed records only -- it does not and cannot recover a missing Original Suggestion link for a pre-existing suggestion (a documented limitation, not a defect).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.10 Suggestion rejection ("I WILL NOT WATCH") and retirement

- **Objective:** Confirm the rejection-threshold/retirement workflow, including `/reject`, `/unreject`, and `/reject`'s Undo Rejection button.
- **Preconditions:** A suggestion with its public confirmation post visible.
- **Steps:**
  1. Click **I WILL NOT WATCH** on the confirmation post as one member; confirm the count updates and the button state changes.
  2. Click it again as the same member; confirm the rejection is removed (toggle, not additive).
  3. As a Watch Party member (WASH Crew inherit this too, but it is not WASH Crew-only), run `/reject suggestion_id:<id>`; confirm the confirmation includes a link back to the suggestion's original public post and an **Undo Rejection** button.
  4. Click **Undo Rejection**; confirm the rejection is removed, the confirmation message updates to say so, and the suggestion post's rejection count refreshes. Confirm another member cannot use your Undo Rejection button.
  5. Reject again, then run `/unreject suggestion_id:<id>` directly to confirm it still works independently of the button.
  6. Reach the configured rejection threshold and confirm the suggestion is retired (archived) and excluded from further selection.
- **Expected Result:** Rejections never double-count per member; the Undo Rejection button works exactly once per rejection and gracefully reports "haven't rejected" if clicked after the rejection was already reverted another way; retirement happens automatically at the configured threshold; retired items remain visible via `/list status:Retired` and can be reactivated through `/add`.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.11 `/remove` and `/edit_suggestion`

- **Objective:** Confirm WASH Crew administrative status-changing/moving/removal works, including the duplicate re-check on a collection move.
- **Preconditions:** WASH Crew role; at least one suggestion; at least two collections in the server.
- **Steps:**
  1. Run `/edit_suggestion`; confirm it shows a read-only summary (title, year, collection, status, IMDb link) plus Change Status, Move to Another Collection, and Cancel -- no title/release year/IMDb link fields to type into. If the suggestion is currently a Vote Winner with a recorded win date, confirm the summary also shows a `Won: <date>` line right below the status.
  2. Choose Change Status; confirm the dropdown offers only Available, Vote Winner (🏆), and Retired (🗄️) (never Rotation Cooldown); pick one and confirm the suggestion's status updates and its public confirmation post's Status field updates in place.
  3. Choose Move to Another Collection; confirm the duplicate check re-runs against the destination collection, and that the suggestion's status is unchanged after the move.
  4. Choose Cancel; confirm nothing changes.
  5. Run `/remove` with a reference number, then again with an exact title; confirm both resolve correctly and archive (not delete) the record.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 3 exactly; history, stable ID, and (for a move) status are preserved throughout.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.12 Rotation Low-Pool Notification

- **Objective:** Confirm WASH proactively notifies WASH Crew when a collection's eligible pool runs low against the finalized threshold (max(10% of Active Watch Items, two configured voting rounds)), fires only once per rotation, resets after a rollover, respects its configured Enabled/Threshold/Destination settings, and is suppressed for very small collections.
- **Preconditions:** A Balanced Random collection large enough that its threshold isn't trivially the whole collection (at least ~15-20 active watch items recommended); Rotation Low-Pool Notification enabled via `/config` (Test 3.2), with a known destination (Admin Channel by default).
- **Steps:**
  1. Reduce the collection's eligible count below the computed threshold (e.g. `/vote start` a few rounds so most items are on Rotation Cooldown, or `/remove` suggestions), then run `/vote start` again.
  2. Observe the configured destination (Admin Channel by default, or the Watch Party Home Channel if switched via `/config`) for a notification naming the collection, the remaining eligible count, the configured candidate count, and a nudge to use `/add`.
  3. Run `/vote start` again without anything else changing; confirm the notification does **not** repeat for the same rotation.
  4. Trigger a rotation rollover (e.g. via Test 5.5b's scenario) and drop the pool below the threshold again; confirm the notification fires again for the new rotation.
  5. Via `/config`, disable the notification; repeat step 1's conditions; confirm nothing is posted.
  6. Re-enable it and switch its destination to the Watch Party Home Channel; confirm the next notification posts there instead of the Admin Channel.
  7. On a Pure Random (`infinite_pool`) collection, drive its eligible count arbitrarily low; confirm no notification is ever sent (Pure Random has no rotation to key the once-per-rotation dedup on).
- **Expected Result:** The notification fires only when the eligible count is genuinely below the threshold, at most once per rotation, and resets naturally once a fresh rotation begins; Enabled/Disabled and Destination are both respected; Pure Random collections never receive it.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.12b Rotation Low-Pool Notification suppression for small collections

- **Objective:** Confirm the notification never fires for a collection whose entire Active pool is already at or below the computed threshold, even though `/database health`'s Low Pool Status still reports it accurately.
- **Preconditions:** A brand-new, small Balanced Random collection (e.g. 3-4 suggestions total, at the documented default candidate count of 3) with the Rotation Low-Pool Notification enabled.
- **Steps:**
  1. Run `/vote start` enough times (with rollovers as needed) that most of the collection has been presented.
  2. Confirm no Rotation Low-Pool Notification is ever posted, no matter how "used up" the small collection gets.
  3. Run `/database health` on the same collection; confirm its Low Pool Status still accurately reports **Insufficient** or **Almost Complete** as appropriate.
- **Expected Result:** The proactive notification stays silent for a collection this small (nothing new to tell an administrator every rotation), while `/database health`'s on-demand report remains accurate.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.13 New-suggestion admission mode

- **Objective:** Confirm the Next Rotation vs. Join Current Rotation setting behaves as configured.
- **Preconditions:** An in-progress rotation (start a vote so one exists) on a database using Rotation Pool or Soft Rotation.
- **Steps:**
  1. With admission mode set to **Next Rotation**, `/add` a new suggestion; confirm it is saved but not selectable until the current rotation completes and a fresh one begins.
  2. Switch to **Join Current Rotation** via `/config`; `/add` another suggestion; confirm it immediately joins the in-progress rotation as unpresented.
- **Expected Result:** Matches [Administration](05-Administration.md)'s "New suggestion admission" section exactly; the setting has no observable effect on a database using Pure Random.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 5. Voting

### 5.1 `/vote start` -- Use Defaults

- **Objective:** Confirm the default voting flow creates a round using the server's configured defaults.
- **Preconditions:** At least 2 eligible suggestions; Voting Defaults configured (Section 2/3).
- **Steps:**
  1. Run `/vote start` -> **Use Defaults**.
- **Expected Result:** A round opens using the configured candidate count, duration, and visibility -- not hardcoded values. The public voting post is WASH's standard embed (yellow accent, no branding) showing visibility, duration/end time, and clean candidate titles (no leading number).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.1b `/vote start` -- Customize This Vote's Candidate Selection Mode override

- **Objective:** Confirm Customize This Vote can override the target collection's Candidate Selection Mode for one round only, without changing the collection's own saved setting, and that the same mode descriptions and visibility explanation from Setup/`/config` appear here too.
- **Preconditions:** A Balanced Random (Rotation Pool) collection with several eligible suggestions.
- **Steps:**
  1. Run `/vote start` -> **Customize This Vote**; confirm a dropdown appears (Candidate Selection Mode) with the same three options and descriptions as Test 2.8, preselected to the collection's actual configured mode (Balanced Random, from this test's precondition), plus a **Continue to Vote Settings** button.
  2. Confirm the message body also explains Visible/Blind (same wording as Test 2.7).
  3. Select **Pure Random**, then press **Continue to Vote Settings**; confirm the familiar candidate count/duration/visibility/reminder modal opens (no candidate-selection field in it).
  4. Submit the modal; confirm the round is created successfully.
  5. Run `/config` -> **Manage Collections** -> this collection -> **Candidate Selection**; confirm it still shows **Balanced Random**, unchanged by the override just used.
- **Expected Result:** The override applies to that one round's nominee selection only; the collection's own configured Candidate Selection Mode is never modified by Customize This Vote.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.2 Minute- and hour-based duration

- **Objective:** Confirm both minute- and hour-based durations work end to end.
- **Preconditions:** No open round.
- **Steps:**
  1. Run `/vote start` -> **Customize This Vote**; confirm both the duration field's label reads "Duration (1m-30d)" and its placeholder starts "Examples: 10m, 1h, 7d", and the reminder-before-close field's label reads "Reminder timing (1m-30d)" with the same placeholder style.
  2. Enter a duration of `10m`, submit, and confirm the round's end time is ~10 minutes out.
  3. Repeat with `1m` (the new one-minute minimum), `30m`, `1h`, `4h`, `12h`, and `3d` to confirm all forms are accepted. Confirm a bare number with no unit (e.g. `3`) is rejected -- an explicit unit is always required.
- **Expected Result:** All forms with an explicit unit are accepted, including minute-level precision down to `1m`; the resulting end time matches; a value outside 1 minute-30 days (e.g. `0m`, `31d`) is rejected with a clear, actionable error.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.3 Visible voting

- **Objective:** Confirm visible-mode standings behave correctly while the round is open.
- **Preconditions:** A round started with `visibility:visible`.
- **Steps:**
  1. Cast votes as two or more members.
  2. Observe the public voting post update after each vote.
  3. Run `/vote status` as WASH Crew.
- **Expected Result:** The post's standings (progress bar, count, percentage) update after every vote; `/vote status` shows candidate titles (never an internal suggestion number) with totals and percentages.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.4 Blind voting

- **Objective:** Confirm blind-mode privacy is preserved throughout.
- **Preconditions:** A round started with `visibility:blind`.
- **Steps:**
  1. Cast a vote and read your own ephemeral confirmation.
  2. Have a second member cast a vote; confirm the first member cannot see it anywhere.
  3. Run `/vote status` while the round is still open.
- **Expected Result:** No standings, counts, or other members' choices are ever revealed while a blind round is open; your own vote confirmation never mentions anyone else's choice; `/vote status` shows only that the round is open and blind, with total votes cast but no per-candidate breakdown.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.5 Candidate selection modes affect nominee choice

- **Objective:** Confirm each candidate-selection mode visibly changes nomination behavior over multiple rounds.
- **Preconditions:** A database with more eligible suggestions than the candidate count; WASH Crew access to `/config`.
- **Steps:**
  1. Set the database's mode to **Balanced Random**; run several rounds; confirm a presented suggestion is not re-nominated until the rotation completes.
  2. Switch to **Soft Rotation**; confirm presented suggestions can reappear but are less frequent than fresh ones.
  3. Switch to **Pure Random**; confirm no exclusion/weighting is applied at all.
- **Expected Result:** Behavior matches [Administration](05-Administration.md)'s "Candidate selection and rotation management" section for each mode.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.5b Rotation rollover when the current rotation can't supply enough candidates

- **Objective:** Confirm a Balanced Random (Rotation Pool) collection with plenty of "Available" suggestions -- most of them just on Rotation Cooldown from a recent round -- automatically rolls over to a fresh rotation and starts the vote, instead of reporting an insufficient-candidates error.
- **Preconditions:** A Balanced Random collection with exactly 3 suggestions.
- **Steps:**
  1. Run `/vote start` with a candidate count of 2; confirm it succeeds and note which 2 suggestions were nominated.
  2. Let the round complete (or use `/vote edit` -> **End Now**).
  3. Confirm `/list` still shows all 3 suggestions as available, but only 1 is unpresented in the current rotation.
  4. Run `/vote start` again with a candidate count of 2.
  5. After the public voting post appears, confirm a separate ephemeral follow-up message (visible only to you) reads "All eligible watch items have now been presented." followed by "Starting Rotation 2." -- and confirm the public voting post itself does **not** mention the rotation at all.
- **Expected Result:** The second `/vote start` succeeds (does not report "not enough eligible suggestions"), automatically starting a fresh rotation and returning the 2 previously-cooled-down suggestions to eligibility. Any suggestion whose confirmation post was showing 🟡 Rotation Cooldown and is not re-nominated this round updates in place to 🟢 Available; Vote Winner and Retired suggestions are never made eligible by this rollover. The Rotation Refresh Notification (step 5) fires exactly once, privately, naming the correct new rotation number. Repeating the same request a third time in a row (with nothing else changed) behaves the same way -- no duplicate or orphaned rotation is created, and no further Rotation Refresh Notification fires since no further rollover happens.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.6 Vote buttons and vote changes

- **Objective:** Confirm the interactive voting buttons work, including changing a vote.
- **Preconditions:** An open round.
- **Steps:**
  1. Click a candidate's button; confirm the ephemeral "recorded" confirmation names the candidate by title.
  2. Click a different candidate's button (a vote change); confirm the confirmation reports the remaining allowed changes.
  3. Attempt another change once the allowance is exhausted; confirm it's clearly rejected.
- **Expected Result:** Button labels show clean candidate titles only (no leading number, no internal ID); the internal candidate mapping remains correct even after changes; the configured vote-change limit is enforced exactly.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.7 `/vote edit` -- change end time, end now, cancel

- **Objective:** Confirm WASH Crew's administrative controls over an in-progress round.
- **Preconditions:** An open round; WASH Crew role.
- **Steps:**
  1. Run `/vote edit` -> **Change End Time**; confirm the menu offers **End Now**, **Shorten Vote**, **Extend Vote**, and **Set Exact End Time**.
  2. Use **Shorten Vote** -> **1 Hour**; confirm the round's end time moves 1 hour *earlier than its current deadline* (not 1 hour from now), and that the public post and a public notice both reflect the new deadline with a Discord relative timestamp shown. Repeat with **Extend Vote** -> **1 Day** and confirm it moves 1 day *later* than the current deadline.
  3. Use **Shorten Vote** -> **Custom...**; confirm the modal is titled "Shorten Vote" with a "Duration" field (placeholder `Examples: 10m, 1h, 7d` -- this field has no fixed range of its own, since Shorten/Extend only rejects a result that would move the end time into the past). Try `10m`, `2h`. Repeat with **Extend Vote** -> **Custom...** (modal titled "Extend Vote"). Confirm a malformed value (e.g. a bare number with no unit) is rejected with a clear error.
  4. On a round closing soon, use **Shorten Vote** with an amount larger than the time remaining; confirm it's rejected with a clear "would move the end time into the past" message rather than silently succeeding.
  5. Use **Set Exact End Time**; confirm the modal presents a single "Discord Timestamp" field with placeholder `<t:1785639600:F>` and help text explaining how to generate one (type `@time` in any normal Discord message box, pick a date/time, then copy the generated timestamp here). Confirm a malformed value (e.g. plain text, or a timestamp missing the `<t:...>` wrapper), and a validly-formatted but past timestamp, are each rejected with a clear message; confirm a valid future timestamp (in any of the standard styles, e.g. `<t:1785639600:F>` or `<t:1785639600:R>`) reschedules correctly.
  6. Start a second round (after the first completes or is cancelled) and use `/vote edit` -> **End Now**; confirm it closes immediately with correct results (a confirmation prompt appears first, since this can't be undone).
  7. Start a third round and use `/vote edit` -> **Cancel Vote**; confirm it's cancelled with no winner announced and the original post's buttons are disabled.
- **Expected Result:** All actions behave exactly as described, and each updates the original voting post appropriately (new deadline / closed with results / cancelled notice). Every new deadline is still stored and scheduled internally as UTC.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.8 Vote completion and winner announcement

- **Objective:** Confirm a round closes automatically at its deadline and announces a winner correctly.
- **Preconditions:** A round started with a short duration (e.g. `1h`, or manually adjusted via `/vote edit` to close within a few minutes for testing).
- **Steps:**
  1. Wait for the round to reach its deadline (or use **End Now**).
  2. Observe the results announcement.
- **Expected Result:** The original post is updated to a closed record with final standings; a single results announcement is posted with the collection, the winner, who suggested it (when recorded), an "About the Winner" embed (poster/runtime/rating/genres when known), and a link back to the original post. No duplicate announcement is posted.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.9 Tie handling

- **Objective:** Confirm a tied round is announced correctly with no thumbnail ambiguity.
- **Preconditions:** Ability to arrange two candidates with equal votes (e.g. a 2-member test server voting for different candidates in a 2-candidate round).
- **Steps:**
  1. Produce a tie and let the round complete.
- **Expected Result:** Both winners are announced ("It's a tie! Winners: ..."), each gets its own "About the Winner" embed, and neither embed shows a poster thumbnail (thumbnails are suppressed for any tie).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 6. Watch Party Scheduling

> **Deferred from v1.** `/watch-party schedule`/`reschedule`/`cancel`/`status` are not registered in v1 -- the scheduled watch party workflow is hidden from the user-facing command set for this release (the underlying implementation is untouched and may return in a later release). Skip this entire section for v1 sign-off; the tests below are kept for whenever this feature is re-enabled.

### 6.1 Schedule a watch party

- **Objective:** Confirm a watch party can be scheduled from a winning or existing suggestion.
- **Preconditions:** WASH Crew role; a known suggestion ID. `watch_item_id` takes a plain integer, not a `#0007`-style reference -- strip the `#` and leading zeros from a suggestion's Reference field (shown on its confirmation post, or in `/remove`'s picker) to get the integer.
- **Steps:**
  1. Run `/watch-party schedule watch_item_id:<id> when:"YYYY-MM-DD HH:MM"` with a near-future time.
- **Expected Result:** A confirmation is shown with the scheduled title and time; `/watch-party status` (Test 6.4) reflects it.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** Flag in Notes if this ID lookup proves confusing in practice -- `/list`'s default view no longer shows reference numbers, so this is the only path today.

### 6.2 Reschedule

- **Objective:** Confirm a scheduled watch party's time can be changed via the watch-party picker.
- **Preconditions:** Test 6.1 passed.
- **Steps:**
  1. Run `/watch-party reschedule when:"YYYY-MM-DD HH:MM"` with a different time.
  2. Choose the watch party from the picker WASH shows (title and scheduled date/time).
- **Expected Result:** The scheduled time updates; the reminder job (Test 6.5) is rescheduled to match, not duplicated.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 6.3 Cancel

- **Objective:** Confirm a scheduled watch party can be cancelled cleanly via the watch-party picker.
- **Preconditions:** Test 6.1 (or 6.2) passed.
- **Steps:**
  1. Run `/watch-party cancel`.
  2. Choose the watch party from the picker WASH shows.
- **Expected Result:** The watch party is cancelled; its reminder job no longer fires; `/watch-party status` no longer reports it as the soonest scheduled item.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 6.4 Watch-party picker and empty state

- **Objective:** Confirm `/watch-party reschedule` and `/watch-party cancel` show a picker of currently scheduled watch parties rather than taking a watch party ID directly, and fail clearly when none are scheduled.
- **Preconditions:** No watch party scheduled initially.
- **Steps:**
  1. Run `/watch-party reschedule` with no watch party scheduled; confirm a clear "no watch parties are currently scheduled" message and no picker appears.
  2. Schedule two or more watch parties (Test 6.1), then run `/watch-party reschedule` again; confirm a picker lists each by title and scheduled date/time, and selecting one affects only that watch party.
  3. Run `/watch-party status`.
- **Expected Result:** With nothing scheduled, both commands fail clearly rather than erroring unhelpfully. With one or more scheduled, a picker is always shown to choose the target -- even with only one. `/watch-party status` reports the soonest-scheduled watch party's title and time, which may not be the one just rescheduled or cancelled if others remain.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 6.5 Reminder creation and delivery

- **Objective:** Confirm a scheduled reminder actually fires.
- **Preconditions:** A watch party scheduled close enough in the future to observe its reminder within the test session (adjust the reminder lead time via configuration if needed).
- **Steps:**
  1. Schedule a watch party with a reminder due within a few minutes.
  2. Wait for the reminder time.
- **Expected Result:** A reminder message is posted at the expected time, once, to the expected channel.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 6.6 Scheduler restart safety

- **Objective:** Confirm a pending watch-party reminder survives a bot restart (see also Section 11).
- **Preconditions:** A watch party scheduled with a reminder still pending.
- **Steps:**
  1. Stop and restart the bot process.
  2. Wait for the reminder's scheduled time.
- **Expected Result:** The reminder still fires at the correct time after the restart -- it is not lost, and it is not duplicated.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 7. Membership Management

> This section was added during the release-validation review: `/join_watch_party` and the `/watch_party` administrative command group are a fully implemented, significant feature area not called out in the original checklist outline. See "Coverage Review" in the delivery notes.

### 7.1 `/join_watch_party` -- Self-Service mode

- **Objective:** Confirm a member can join (and leave) the Watch Party role directly when Self-Service mode is configured.
- **Preconditions:** Watch Party join mode set to Self-Service (Test 2.2 or `/config`).
- **Steps:**
  1. As a member without the Watch Party role, run `/join_watch_party`.
  2. Run it again to leave.
- **Expected Result:** The role is granted immediately on the first run, and removed on the second (if `allow_self_leave` is enabled).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 7.2 `/join_watch_party` -- Manual and Approval-Required modes

- **Objective:** Confirm the other join modes behave as documented.
- **Preconditions:** Join mode set to Manual, then separately to Approval-Required.
- **Steps:**
  1. In Manual mode, run `/join_watch_party` as a member; confirm it explains that WASH Crew must add them manually (see Test 7.3).
  2. In Approval-Required mode, run `/join_watch_party`; confirm a request is created and appears in `/watch_party pending`.
- **Expected Result:** Manual mode never grants the role directly. Approval-Required creates a pending request visible to WASH Crew, and (if an admin channel is configured) posts it there.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 7.3 `/watch_party` administrative subcommands

- **Objective:** Confirm each WASH Crew membership-administration subcommand works.
- **Preconditions:** WASH Crew role; at least one pending request (Test 7.2) and one existing member.
- **Steps:**
  1. Run `/watch_party members` -- confirm the current membership list appears.
  2. Run `/watch_party pending` -- confirm the pending Approval-Required request appears.
  3. Approve or deny it, then run `/watch_party approved` and `/watch_party denied` to confirm it moved to the correct list.
  4. Run `/watch_party add member:<user>` and `/watch_party remove member:<user>` to manually grant/revoke the role.
  5. Run `/watch_party search member:<user>` to confirm membership history is reported.
- **Expected Result:** Every subcommand works as named and is restricted to WASH Crew.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 7.4 Discord-Managed join mode

- **Objective:** Confirm WASH correctly defers to an externally managed role.
- **Preconditions:** Join mode set to Discord-Managed.
- **Steps:**
  1. Run `/join_watch_party` as a member.
- **Expected Result:** WASH explains that this server manages the role outside of WASH (no action taken by the bot).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 8. Statistics

### 8.1 Server statistics

- **Objective:** Confirm the default statistics type reports server-wide data.
- **Preconditions:** At least one completed voting round and one scheduled watch party.
- **Steps:**
  1. Run `/stats` (default `type:Server`).
- **Expected Result:** Watch parties, voting rounds by status/visibility, participation, average candidates per round, and average vote duration are reported; response is ephemeral by default.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 8.2 Member statistics and self-publish

- **Objective:** Confirm member statistics are self-only, but self-publishable.
- **Preconditions:** A member who has submitted suggestions and cast votes.
- **Steps:**
  1. Run `/stats type:Member` -- confirm only your own data is shown, with no option to target another member.
  2. Run `/stats type:Member public:true` -- confirm you (any member, not just WASH Crew) can post your own statistics publicly.
- **Expected Result:** Matches exactly; WASH Crew cannot retrieve another member's statistics under any circumstance.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 8.3 Suggestion, Rotation, and Database statistics

- **Objective:** Confirm the remaining three statistic types work and respect the public-posting rule.
- **Preconditions:** WASH Crew role; an existing suggestion and database.
- **Steps:**
  1. Run `/stats type:Suggestion suggestion:<reference or title>`.
  2. Run `/stats type:Rotation` and `/stats type:Database`.
  3. Repeat one with `public:true` as WASH Crew, then attempt it as a regular member and confirm it's rejected.
- **Expected Result:** Each type reports the fields documented in [Administration](05-Administration.md) Section 10; public posting for these three types is WASH Crew only.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 8.4 Empty-state behavior

- **Objective:** Confirm statistics degrade gracefully with no historical data.
- **Preconditions:** A freshly configured database/guild with no votes, suggestions, or watch parties yet (or a new test database created for this purpose).
- **Steps:**
  1. Run each `/stats` type against the empty data.
- **Expected Result:** Every type reports a clear "no data yet" style result -- never a raw error, exception, or division-by-zero artifact (e.g. average vote duration with zero rounds).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 9. Backup & Restore

### 9.1 `/backup`

- **Objective:** Confirm a full manual backup can be created and downloaded.
- **Preconditions:** WASH Crew role; some existing data (suggestions, a completed vote, etc.).
- **Steps:**
  1. Run `/backup`.
- **Expected Result:** An ephemeral response reports the filename, creation time, and type; a `.zip` file named `Watch_Party_Manager_Backup_YYYY-MM-DD_HH-MM-SS.zip` is attached and downloadable.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.2 Attachment download and manifest contents

- **Objective:** Confirm the backup archive is valid and self-describing.
- **Preconditions:** Test 9.1 passed.
- **Steps:**
  1. Download the attachment.
  2. Open the `.zip` and inspect `manifest.json`.
- **Expected Result:** The archive opens cleanly; `manifest.json` includes `project_name`, `application_version`, format version, `backup_type: full`, `kind: manual`, `created_at`, `guild_id`, and a `files` list with per-file checksums.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.3 `/restore` -- full restore with confirmation

- **Objective:** Confirm the full-restore flow requires explicit confirmation and never silently overwrites data.
- **Preconditions:** A known-good backup from Test 9.1; some data changed since it was made (so the restore is observable).
- **Steps:**
  1. Run `/restore backup_filename:<name>` (or upload the `.zip` via `backup_file`).
  2. Review the validation summary WASH shows before doing anything.
  3. Click **Restore**.
  4. Restart the bot (see Test 11.1) and confirm the data matches the backup.
- **Expected Result:** Nothing changes until **Restore** is explicitly clicked; a pre-restore safety backup is created automatically; after restart, live data matches the restored backup exactly.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.4 `/database backup` and `/database restore`

- **Objective:** Confirm single-database backup/restore, including both Merge and Replace modes.
- **Preconditions:** At least one suggestion database with several suggestions.
- **Steps:**
  1. Run `/database backup`, choose the database from the picker.
  2. Add a new suggestion to the same database (so Merge has something new to reconcile against).
  3. Run `/database restore mode:Merge` with the backup; confirm existing suggestions are untouched and only non-conflicting ones are added.
  4. Repeat with `mode:Replace`; confirm the database is fully overwritten to match the backup.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 9's Merge/Replace description exactly; a safety backup is made before Replace.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.5 `/database reset`

- **Objective:** Confirm the typed-confirmation-gated reset works and only affects the chosen database.
- **Preconditions:** A disposable test database with a few suggestions.
- **Steps:**
  1. Run `/database reset`, choose the database, confirm the shown "would remove" count.
  2. Click **Reset**, then submit anything other than `RESET` in the modal -- confirm nothing changes.
  3. Repeat and type `RESET` exactly.
- **Expected Result:** Only an exact `RESET` proceeds; the database's own record/name/configuration and every other database are untouched; a safety backup is made first.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.6 `/factory_reset`

- **Objective:** Confirm the full-server reset works and correctly requires `/setup` again afterward.
- **Preconditions:** A disposable test server (this is destructive) with existing configuration and data.
- **Steps:**
  1. Run `/factory_reset`, review the shown removal count, click **Factory Reset**, type `RESET` exactly.
  2. Run `/setup` afterward.
- **Expected Result:** Every WASH-managed record for the server is removed (server configuration, databases, suggestions, votes, membership requests, scheduled items); a safety backup is made first; `/setup` treats the server as brand new.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.7 `/import` (cross-instance)

- **Objective:** Confirm importing another instance's backup works in both Merge and Replace modes.
- **Preconditions:** A full backup `.zip` from a *different* WASH instance/server (or a second local test server acting as the source).
- **Steps:**
  1. Run `/import backup_file:<upload>`.
  2. Review the validation summary.
  3. Choose **Merge**; confirm the reported imported/skipped/reassigned counts.
  4. Repeat with **Replace**, typing `REPLACE` exactly to confirm.
- **Expected Result:** Only portable data (databases, their suggestions/configuration, vote rounds) is imported; this server's own configuration (roles, channels, server ID) is never changed; ID collisions are reassigned automatically; results are reported once and not persisted.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.8 Export

- **Objective:** Confirm the documented export model (there is no separate export command).
- **Preconditions:** None.
- **Steps:**
  1. Confirm no `/export` command exists in `/help`.
  2. Confirm `/backup`'s output is what another instance would use as the "export" side of an `/import`.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 8: `/backup` doubles as the export mechanism; there is no dedicated export command.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.9 Failure handling

- **Objective:** Confirm corrupt/invalid input fails safely with no data loss.
- **Preconditions:** A deliberately corrupted `.zip` (e.g. truncate a valid backup file, or edit its manifest).
- **Steps:**
  1. Attempt `/restore` with the corrupted file.
  2. Attempt `/database restore` with a full (not single-database) backup, and vice versa.
  3. Attempt `/database restore` with a backup from a different server.
- **Expected Result:** Each is rejected with a clear, specific message (see the troubleshooting table in [Administration](05-Administration.md) Section 9); live data is never modified by a failed or rejected attempt.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.10 Automatic backups actually run on schedule

- **Objective:** Confirm the automatic-backup scheduler creates a real backup at the configured interval, not just a cosmetic setting.
- **Preconditions:** Automatic backups enabled (Test 2.10b or 3.3b) with a short interval configured for testing purposes (e.g. edit the interval down, or use the shortest allowed value).
- **Steps:**
  1. Note the current contents of `data/backups/`.
  2. Wait until the configured interval has elapsed.
  3. Inspect `data/backups/` again.
- **Expected Result:** A new backup archive appears with `kind: scheduled` in its manifest (distinct from a `kind: manual` backup created via `/backup`), with no WASH Crew action required.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.11 Automatic backup retention pruning

- **Objective:** Confirm automatic backups beyond the configured retention count are pruned, while manual backups are unaffected.
- **Preconditions:** Automatic backups enabled with a small retention count (e.g. 2 or 3) configured; enough elapsed intervals (Test 9.10) to exceed that count.
- **Steps:**
  1. Let enough scheduled backups accumulate to exceed the configured retention count.
  2. Inspect `data/backups/`.
  3. Create a manual `/backup` in the same window.
- **Expected Result:** Only the newest N scheduled backups are kept (N = retention count); older scheduled backups are removed automatically. Manual backups are tracked in a separate pool and are never pruned by the automatic-backup retention setting.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.12 Disabling stops future automatic backups without deleting existing ones

- **Objective:** Confirm disabling automatic backups (via `/config` -> Backup Defaults) stops future scheduled backups but leaves existing ones untouched, and that `/backup` keeps working.
- **Preconditions:** Automatic backups enabled with at least one scheduled backup already created (Test 9.10).
- **Steps:**
  1. Note the existing scheduled backups in `data/backups/`.
  2. Disable automatic backups via `/config` -> Backup Defaults.
  3. Wait past what would have been the next scheduled run.
  4. Run `/backup` manually.
- **Expected Result:** No new scheduled backup is created after disabling; the previously existing scheduled backups are still present; manual `/backup` succeeds regardless of the automatic setting.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.13 Re-enabling resumes scheduling, and restart reconciles correctly

- **Objective:** Confirm re-enabling automatic backups resumes scheduling using the saved settings, and that a bot restart neither loses nor duplicates the scheduled job.
- **Preconditions:** Test 9.12 completed (automatic backups currently disabled).
- **Steps:**
  1. Re-enable automatic backups via `/config` -> Backup Defaults, keeping (or changing) the interval/retention count.
  2. Confirm a new automatic backup is created at the next configured interval.
  3. Restart the bot process.
  4. Confirm exactly one automatic-backup job is active afterward (no duplicate), and that it still fires at the correct time.
- **Expected Result:** Re-enabling resumes scheduling from the saved or newly configured interval/retention with no manual intervention beyond the `/config` change; a restart reconciles the schedule against the current configuration without creating a duplicate job or losing the schedule entirely.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 10. Permissions

### 10.1 Watch Party member commands

- **Objective:** Confirm the participant command set is available to Watch Party members (and WASH Crew, who inherit it).
- **Preconditions:** A member with only the Watch Party role.
- **Steps:**
  1. Run `/add`, `/list`, and `/stats` as this member.
  2. Run `/help` and confirm the shown command list matches what's actually usable.
- **Expected Result:** All three commands work; `/help` accurately reflects this member's permission tier.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 10.2 WASH Crew commands

- **Objective:** Confirm WASH Crew-only commands are usable by WASH Crew and inherit member-level access too.
- **Preconditions:** A member with the WASH Crew role.
- **Steps:**
  1. Run a representative sample: `/vote start`, `/vote status`, `/database add`, `/config`, `/backup`.
  2. Confirm the same member can also run `/add`/`/list`/`/stats` (inherited member access).
- **Expected Result:** All succeed; `/help` shows the full WASH Crew command list.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 10.3 Unauthorized users

- **Objective:** Confirm a member with neither role is correctly restricted.
- **Preconditions:** A member with no configured role.
- **Steps:**
  1. Run `/add` and a WASH Crew-only command (e.g. `/vote start`).
- **Expected Result:** `/add` is rejected (member role required); the WASH Crew command is rejected with a message distinguishable from "role not configured" (Test 10.4). `/help`, `/about`, and `/join_watch_party` remain available to everyone.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 10.4 Fail-closed when a role is unconfigured

- **Objective:** Confirm restricted commands fail closed -- including for server administrators -- when the relevant role isn't configured.
- **Preconditions:** A test server where `WATCH_PARTY_MEMBER_ROLE_ID`/the wizard's role step was never set (or temporarily unset).
- **Steps:**
  1. As a server administrator with no explicitly configured role, run `/add`.
- **Expected Result:** Rejected with a clear "not configured" message -- administrator status alone does not bypass the check.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 10.5 DM behavior

- **Objective:** Confirm guild-only commands behave correctly when run in a DM with the bot.
- **Preconditions:** None.
- **Steps:**
  1. DM the bot and run `/add` (or any other guild-scoped command).
- **Expected Result:** A clear "this command can only be used in a server" message -- no crash, no silent failure.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 10.6 `/about` expanded diagnostics (WASH Crew only)

- **Objective:** Confirm the Health/Configuration/Runtime sections are visible only to WASH Crew, and are accurate.
- **Preconditions:** A fully configured server (post-Section 2) with an open voting round.
- **Steps:**
  1. Run `/about` as a regular Watch Party member; confirm only identity/documentation links appear.
  2. Run `/about` as WASH Crew.
- **Expected Result:** The WASH Crew view additionally shows Health (Discord connection, scheduler status, interactive-voting restoration state, OMDb configured or not), Configuration (active database, database/watch-item/scheduled-watch-party counts, whether a round is open -- correctly reflecting the open round from Preconditions), and Runtime (Python/discord.py versions, uptime, server name).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 11. Restart Testing

### 11.1 Bot restart with an open voting round

- **Objective:** Confirm interactive voting controls survive a restart.
- **Preconditions:** An open voting round with its public post still live.
- **Steps:**
  1. Stop the bot process.
  2. Start it again.
  3. Click a vote button on the still-open post.
- **Expected Result:** The vote is recorded correctly after restart -- the persistent view was re-registered, not lost.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 11.2 Scheduled jobs survive restart

- **Objective:** Confirm pending close-vote, vote-reminder, and watch-party-reminder jobs are not lost or duplicated by a restart.
- **Preconditions:** An open vote with a pending reminder, and/or a scheduled watch party with a pending reminder.
- **Steps:**
  1. Restart the bot.
  2. Wait for each pending job's scheduled time.
- **Expected Result:** Each job fires exactly once, at the correct time, whether or not a restart happened in between.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 11.3 Configuration loads correctly after restart

- **Objective:** Confirm guild configuration is read fresh (not lost) on the next startup.
- **Preconditions:** A fully configured server.
- **Steps:**
  1. Restart the bot.
  2. Run `/config` and compare every section against its pre-restart value.
- **Expected Result:** Configuration is identical before and after restart.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 11.4 Suggestion rejection buttons survive restart

- **Objective:** Confirm the "I WILL NOT WATCH" button on existing confirmation posts still works after a restart.
- **Preconditions:** An existing suggestion confirmation post.
- **Steps:**
  1. Restart the bot.
  2. Click **I WILL NOT WATCH** on a pre-restart post.
- **Expected Result:** The click is handled correctly (rejection recorded, button state updates).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 12. Documentation Spot Check

For each document, confirm it matches actual current behavior observed during Sections 1-11. Record any mismatch in Notes rather than fixing it here -- this is a verification pass, not an editing pass.

### 12.1 README

- **Steps:** Compare the command list, current-status line (version, test count), and feature summary against what you actually observed.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 12.2 Installation Guide

- **Steps:** Confirm every step you followed in Section 1 matches the guide with no missing or stale step.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 12.3 Administration Guide

- **Steps:** Spot-check the sections exercised in Sections 3-9 above (voting, backup/restore, statistics) against actual behavior.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 12.4 Expanded Help

- **Steps:** Confirm terminology used matches what `/help` and the bot's own messages actually say.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 12.5 Command Reference

- **Steps:** Confirm every command run during this checklist appears, with the correct required role.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 12.6 Developer Guide

- **Steps:** Confirm the documented test-run command and current test-count baseline match what the automated suite actually reports.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 12.7 Project State

- **Steps:** Confirm the functional-requirement status table matches what you actually observed working (or not) in Sections 2-11.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 12.8 Configuration Specification (`guild_configuration_spec.md`)

- **Steps:** Confirm the documented Voting Defaults schema (fields, defaults, validation ranges) matches actual persisted behavior observed in Section 2/3 (in particular: field name and unit for voting duration, and the default voting visibility).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 12.9 CHANGELOG

- **Steps:** Confirm every feature exercised in this checklist has a corresponding entry, and that the version heading matches the package version actually being released (Test 13.4).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 13. Final Acceptance

Complete this section only after every prior section has been executed at least once.

- [ ] All tests passed (or every failure has a filed, triaged issue -- see Notes)
- [ ] Documentation verified (Section 12 complete, no unresolved mismatches)
- [ ] No known release blockers remain open
- [ ] Version number verified (package version, README, and CHANGELOG heading all agree)
- [ ] CHANGELOG finalized (`[Unreleased]` resolved into the actual release entry)
- [ ] README finalized (test count and feature summary current)
- [ ] Git tag ready (tag name and target commit confirmed)
- [ ] GitHub release ready (release notes drafted from CHANGELOG)

**Sign-off notes:**

_______________________________________________________________
_______________________________________________________________
_______________________________________________________________

**Tester:** _______________________ **Date:** _______________

**Overall Result:**

- [ ] PASS
- [ ] FAIL
