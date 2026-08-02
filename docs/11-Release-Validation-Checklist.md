# Watch Party Manager

## Release Validation Checklist

| Property | Value |
| --- | --- |
| Document | Release Validation Checklist |
| File | `11-Release-Validation-Checklist.md` |
| Version | 1.0 |
| Status | Active |
| Last Updated | August 2026 |
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
- **Expected Result:** `/about` shows WASH's identity and documentation links (no Health/Configuration/Runtime sections yet, since the member isn't WASH Crew). `/help` shows only the commands available to an unconfigured member (`/help`, `/about`, `/join watch party`).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 1.11 Command tree matches the documented structure (Command Structure Cleanup)

- **Objective:** Confirm Discord's own slash-command autocomplete shows exactly the documented command tree -- grouped commands registered correctly, obsolete underscore commands gone, no duplicate command roots, and no Discord naming-rule violations.
- **Preconditions:** Section 1.9 passed (bot online, commands synced).
- **Steps:**
  1. Type `/database` in Discord's command box; confirm autocomplete offers exactly `add`, `manage`, `list`, `move`, `backup`, `restore`, `remove`, `reset` as subcommands, and that typing `/database_add`, `/database_backup`, `/database_restore`, `/database_reset`, `/database_list`, or `/database_remove` (the old top-level names) finds nothing.
  2. Type `/vote`; confirm autocomplete offers exactly `start`, `status`, `edit`, and that `/start_vote`, `/vote_status`, `/edit_vote`, and the pre-release `/voting` group no longer exist.
  3. Type `/watch-party`; confirm autocomplete offers exactly `schedule`, `status`, `reschedule`, `cancel`, and that `/schedule_watch_party`, `/reschedule_watch_party`, `/cancel_watch_party`, `/watch_party_status` no longer exist.
  4. Type `/membership`; confirm it still exists, separately from `/watch-party` (hyphen), and still offers `members`, `pending`, `approved`, `denied`, `add`, `remove`, `search` -- membership administration is unaffected by this cleanup.
  5. Confirm every other command named in [Command Reference](10-Command-Reference.md) (e.g. `/about`, `/add`, `/maintenance backup`, `/config`, `/help`, `/list`, `/random watch`, `/maintenance restore`, `/setup`, `/stats`, `/suggestion remove`, `/suggestion edit`, `/reject`, `/unreject`, `/maintenance repair`, `/maintenance reset`, `/maintenance import`, `/join watch party`) still appears exactly as documented, unchanged.
- **Expected Result:** The live Discord command tree matches [Command Reference](10-Command-Reference.md) exactly -- no obsolete command remains reachable under its old name, no command was accidentally duplicated under both an old and new name, and `/membership`/`/watch-party` coexist as the two distinct, valid command names documented there.
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
  2. Confirm WASH redirects to the specific failing step (e.g. "Step 3 of 11: Admin Channel") with a clear, actionable error -- not a generic failure, and not silently discarding the review state.
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
- **Expected Result:** The save confirmation is clear and non-destructive (setup is not marked complete). Running `/setup` again shows a resume prompt naming how many of 11 steps are complete and the current step; **Continue Setup** returns to exactly where you left off with all prior answers intact.
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

### 2.5 Collections (suggestion destination)

- **Objective:** Confirm a collection (internally an ordinary `SuggestionDatabase` record) is created with its own suggestion thread automatically created as a sibling under WASH's home channel, and that thread is immediately saved as that collection's Suggestion Destination -- no separate destination choice, and no additional configuration needed before `/add` works in it.
- **Preconditions:** Test 2.4b passed, reached the Collections step.
- **Steps:**
  0. Before choosing anything, confirm the buttons appear in this order: **Create New** (primary/highlighted style), **Select Existing** (secondary style), **Back**, **Save & Finish Later**, then **Cancel Setup** (danger/red style, visually separated from the rest).
  1. Choose **Create New**; confirm a "What type of collection would you like to create?" screen appears with **Movies (Recommended)**, **TV Shows**, **Special Collection**, **Custom**, and **Import Existing Database**.
  2. Choose **Movies**; confirm no name prompt and no destination-choice screen appear -- a thread named "Movie Suggestions" (the descriptive default, not the bare type name "Movies") is created directly under the home channel and the wizard advances. Repeat with **TV Shows** and confirm its default thread name is "TV Suggestions".
  3. In the server, open the new "Movie Suggestions" thread and run `/add` with any title; confirm the public suggestion post is created immediately in that thread, with no "no suggestion channel is configured" error.
  4. Separately, repeat with **Special Collection** or **Custom**; confirm a name prompt appears first (still fully custom, unaffected by the Movies/TV Shows default naming), then the thread is created using that name.
  5. Separately, click **Import Existing Database**; confirm WASH explains that `/maintenance import` must be run as its own command (Discord does not allow attaching a file from inside this wizard) and offers a way back to the type-choice screen.
- **Expected Result:** The Collections step's buttons appear in the order and styles described in step 0 -- **Create New** is the recommended/default (primary) action, **Select Existing** is secondary, and **Cancel Setup** is styled as danger. Movies/TV Shows create a collection using their descriptive default thread name ("Movie Suggestions"/"TV Suggestions") with no name prompt; Special Collection/Custom collect a name first; every creation path ends with the collection's suggestion thread created as a sibling under the home channel and immediately persisted as that collection's Suggestion Destination (and, internally, all remain ordinary `SuggestionDatabase` records). `/add` works in the new thread right away. Import Existing never fakes an in-wizard upload.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.5c Nested Collections screens expose Back and Save & Finish Later

- **Objective:** Confirm the "what type of collection" screen and the "select an existing collection" screen (both nested under the Collections step) offer **Back** and **Save & Finish Later**, not just **Cancel Setup** -- live testing found these two buttons missing, leaving a full destructive cancel as the only way to exit safely.
- **Preconditions:** Test 2.5 passed.
- **Steps:**
  1. From the Collections step, click **Create New** to reach the "what type" screen; confirm **Back**, **Save & Finish Later**, and **Cancel Setup** all appear alongside the five type buttons.
  2. Click **Back**; confirm it returns to the Collections step (Create New / Select Existing), not an earlier wizard step, and every previously entered value (roles, channels) is still intact.
  3. Return to the "what type" screen and click **Save & Finish Later**; confirm setup exits without creating a collection and without marking setup complete, and `/setup` later resumes at the Collections step.
  4. Repeat steps 1-3 for the "select an existing collection" screen (reached via **Select Existing**, with at least one collection already present).
- **Expected Result:** Both nested screens expose the same Back/Save & Finish Later/Cancel Setup trio as every top-level step; Back returns to the Collections step's own choice screen; Save & Finish Later never creates a collection or marks setup complete.
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

- **Objective:** Confirm `/config`'s Watched Item Archive section sets a server-wide fallback, and that a collection's own override takes precedence when both are set.
- **Preconditions:** Setup completed; at least one collection exists.
- **Steps:**
  1. Run `/config` -> **Watched Item Archive**; set it to a channel, or clear it.
  2. Run `/config` -> **Collections** -> a collection -> its Watched Item Archive; confirm the screen shows both "This collection's own override" and "Currently effective" values.
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
  0. Before opening the modal, confirm the step's body text explains: "Visible -- everyone can see vote totals while voting is active. Blind -- results stay hidden until voting closes." **and** explains duration in full: "Durations combine a number with a unit -- minutes (m), hours (h), or days (d). Examples: 10m, 1h, 7d." (Vote Duration Wording: the field itself is always pre-filled, so its placeholder would never actually be shown -- the full minutes/hours/days explanation lives on this screen instead of being crammed into the field's label.)
  1. Open the modal (**Set Voting Defaults**); confirm the duration field's label reads simply "Vote duration (1m-30d)" -- just the field name and its valid range, no squeezed-in examples -- and the field is pre-filled `1d` (not "1 day") on a brand-new server.
  2. Enter a candidate count (try an in-range value, e.g. `4`).
  3. Enter a duration using WASH's shared duration syntax (try `4h`, then separately `3d`, then `3 days` -- confirm all are accepted and mean what you'd expect). Confirm a bare number with no unit (e.g. `3`) is rejected -- an explicit unit is always required. Separately, confirm `1m` (the new one-minute minimum) is accepted.
  4. Set visibility to `visible` or `blind`.
  5. Submit, then reopen the modal and confirm the just-saved duration redisplays in the same compact form (e.g. `4h`, not "4 hours").
- **Expected Result:** Values in range are accepted; the modal's duration field is pre-filled `1d` on a brand-new server and always redisplays previously saved durations in compact form. An out-of-range duration (e.g. `31d` / `0m`) is rejected with a clear, specific error naming the actual 1 minute-30 day range; a unitless value is rejected with a clear syntax error that shows only the `10m`/`1h`/`7d` examples (never `1w` or a duplicate day example).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.8 Nominee selection mode and its help text

- **Objective:** Confirm all three nominee-selection modes are selectable from the dropdown, each with a plain-language description, and that **Favor New Additions** is the pre-filled default.
- **Preconditions:** Reached the Voting Defaults step (before pressing **Set Voting Defaults**).
- **Steps:**
  1. Open the nominee-selection dropdown; confirm it lists exactly **Favor New Additions (Recommended)**, **Favor Older Additions**, and **Pure Random**, with **Favor New Additions (Recommended)** preselected.
  2. Confirm each option shows a short description beneath its label: Favor New Additions -- "Leans toward suggestions added recently; older ones stay eligible, just at a lower chance."; Favor Older Additions -- "Leans toward suggestions that have waited the longest; newer ones stay eligible, just at a lower chance."; Pure Random -- "Chooses completely at random from eligible suggestions, with no preference or exclusion."
  3. Select each mode in turn, press **Set Voting Defaults**, and confirm the modal opens (candidate count/duration/visibility only -- nominee selection is not one of its fields).
- **Expected Result:** All three modes are selectable from the dropdown with the exact descriptions above; the chosen mode is saved correctly regardless of which one was picked -- see [Administration](05-Administration.md)'s "Nominee selection" section.
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
  3. Before pressing Enable, confirm the step's body text explains duration in full: "Durations combine a number with a unit -- minutes (m), hours (h), or days (d). Examples: 10m, 1h, 7d." Go back and press **Enable Vote-Ending Reminder (Recommended)**; confirm a modal opens with exactly one field, labeled simply "Reminder before close (1m-30d)" (no squeezed-in examples -- the full explanation already lives on the screen behind it), pre-filled with a compact value (e.g. `1d`, not "1 day").
  4. Submit using WASH's shared duration syntax -- try minute precision (e.g. `10m`, `30m`, and the new one-minute minimum `1m`) as well as hours/days (e.g. `1h`, `7d`), and confirm an out-of-range or malformed value is rejected with a clear error while keeping the Enable choice (no need to re-pick Enable/Disable).
- **Expected Result:** Disable saves immediately with no modal; Enable opens a single-field modal that accepts valid values (minute precision, not just whole hours) and rejects invalid ones with a clear error; the field's label and pre-filled value are both in compact form, with the fuller minutes/hours/days explanation shown on the screen before the modal.
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
- **Expected Result:** Every section (roles, join mode, admin channel, home channel, suggestion database, Watched Item Archive, voting defaults including nominee selection, reminders, backup) is shown accurately, using natural-language duration (e.g. "4 hours" or "3 days", never "72 hours"). The Automatic Backups line reads "Automatic Backups: Disabled" if you chose Disable in Test 2.10b, or "Automatic Backups: Every N day(s), keep M" (matching the configured interval/retention) if you chose Enable. Save marks setup complete and shows a final completion summary matching the same values, including the same Automatic Backups line.
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
  2. Run `/setup`; on the Collections step, choose **Select Existing** and pick the collection created earlier.
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
- **Expected Result:** A menu listing every section (WASH Crew Role, Watch Party Role, Watch Party Join Mode, Admin Channel, Home Channel, Collections, Watched Item Archive, Voting Defaults, "I Won't Watch" Settings, Reminder Defaults, Backup Defaults, Eligible Pool Warning) appears, each showing its current status, with no description text under any option.
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

### 3.3a `/config`'s main menu is a clean numbered list with no descriptions

- **Objective:** Confirm the main `/config` dropdown shows only numbered section names -- explanatory text (e.g. Visible/Blind) has moved to each section's own screen.
- **Preconditions:** Setup completed.
- **Steps:**
  1. Run `/config`; open the section dropdown without selecting anything yet.
  2. Inspect every entry's description text.
  3. Open **Voting Defaults** and confirm the Visible/Blind explanation ("Visible: totals shown live. Blind: hidden until voting closes." or equivalent) appears on that screen instead.
- **Expected Result:** No main-menu entry (including Voting Defaults) shows description text; the Visible/Blind explanation is present on the Voting Defaults screen itself.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.3c `/config` Voting Defaults exposes Nominee Selection, scoped correctly

- **Objective:** Confirm `/config`'s Voting Defaults screen mirrors the Setup Wizard by also exposing Nominee Selection, resolved/scoped per collection, and never applied to the wrong collection.
- **Preconditions:** Setup completed; at least two collections exist.
- **Steps:**
  1. With only one collection in the server, run `/config` -> **Voting Defaults**; confirm the screen shows that collection's current Nominee Selection preselected in a dropdown, alongside Vote Visibility, with no separate collection-choice step.
  2. Create a second collection. Run `/config` -> **Voting Defaults** again; confirm WASH now first asks which collection's Nominee Selection to edit, with each option's description naming its current mode.
  3. Choose collection A, change its Nominee Selection to a different mode, and submit the modal (candidate count/duration) alongside it.
  4. Re-run `/config` -> **Voting Defaults** and check collection B's Nominee Selection (via the picker, or `/config` -> Collections).
- **Expected Result:** Collection A's Nominee Selection changed to the new mode; collection B's Nominee Selection is unchanged; the guild-wide candidate count/duration/visibility submitted in the same step apply regardless of which collection was chosen.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.3b `/config` Backup Defaults: enable, disable, and re-enable

- **Objective:** Confirm `/config`'s Backup Defaults section can enable, disable, and re-enable automatic backups, and that the summary and actual scheduling behavior both reflect the current setting.
- **Preconditions:** Section 2 completed (automatic backups enabled or disabled, either way, from Test 2.10b).
- **Steps:**
  1. Run `/config` -> **Backup Defaults**; confirm the summary line reads either "Automatic Backups: Disabled" or "Automatic Backups: Every N day(s), keep M" matching the current setting.
  2. Choose **Disable Automatic Backups** (if not already disabled); confirm the summary updates to "Automatic Backups: Disabled" and that any existing backup files in `data/backups/` are still present afterward.
  3. Run `/maintenance backup` while automatic backups are disabled; confirm it still succeeds.
  4. Choose **Enable Automatic Backups** again, setting an interval and retention count; confirm the summary updates to "Automatic Backups: Every N day(s), keep M" matching what you entered.
- **Expected Result:** Disabling never deletes existing backups and never blocks manual `/maintenance backup`; enabling and disabling both take effect immediately and are reflected accurately in the summary line.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4 Database management: create, list

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
- **Expected Result:** The type screen correctly excludes already-used standard types while always offering Special Collection/Custom; the new thread is created as a sibling under WASH's configured Home Channel and the collection is created on it immediately (rejects a duplicate name); **Use Current Thread** is enabled only inside a thread (a text channel, even the Home Channel, never qualifies) and disabled rather than broken otherwise; **Use Existing Thread** never offers a text channel; the list shows name, Active/Inactive status, and item count.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4b Move Collection (`/database manage`)

- **Objective:** Confirm a collection's suggestion destination can be moved to a different thread without affecting anything else about it, and that WASH's Home Channel can never become a destination. Move Collection has no separate top-level command -- it's only reachable through `/database manage`.
- **Preconditions:** At least one collection with existing suggestions and at least one completed vote round; a Home Channel is configured.
- **Steps:**
  1. Run `/database manage`, choose the collection from the picker, then choose **Move Collection**.
  2. Confirm the destination screen shows exactly **Create New Thread (Recommended)**, **Use Current Thread**, and **Use Existing Thread**, in that order (same order and options as `/database add`) -- no channel-based option is offered.
  3. Choose **Use Existing Thread** and select a destination thread not already used by another collection.
  4. Confirm the move succeeds; run `/add` in the new destination and confirm the suggestion is created there.
  5. Confirm the collection's earlier suggestion posts (from before the move) are still visible in their original thread, untouched.
  6. Attempt a second move to a destination already used by another collection; confirm it's rejected with a clear "already routed" message and nothing changes.
  7. Repeat step 1-4 choosing **Create New Thread** instead; confirm the new thread is created under WASH's configured Home Channel and the suggested default name can be renamed before creation (renaming here must not change the collection's own name -- only `/database add`'s Create New Thread renames the collection).
  8. Reach Move Collection again from inside a thread; confirm **Use Current Thread** is enabled and, when chosen, moves the collection's destination to that same thread with no further prompts.
  9. Reach Move Collection again from a plain text channel, including WASH's own Home Channel; confirm **Use Current Thread** stays disabled in both cases.
  10. Attempt to route a collection's suggestion destination to WASH's configured Home Channel (e.g. via `/config` -> Collections -> Suggestion Destination, if reachable, or by any other means available); confirm it's clearly rejected and nothing changes.
  11. Confirm `/database move` no longer exists as a slash command at all (it does not autocomplete after typing `/database `).
- **Expected Result:** Only the collection's suggestion destination changes -- its database ID, suggestions, statuses, vote history, and statistics are all unchanged (spot-check via `/stats type:Collection` or `/config` -> Collections before and after); existing Discord suggestion posts are never moved; only suggestions added after the move appear in the new destination; a duplicate destination is rejected cleanly; **Use Current Thread** behaves identically to `/database add`'s version (same enable/disable rule) but never renames the collection; WASH's Home Channel is never accepted as a destination, from any path; there is no standalone `/database move` command.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4b2 Context Resolution Audit: commands stop resolving a moved collection's old location

- **Objective:** Confirm the bug fix -- a collection moved into its own thread must no longer resolve from wherever it used to be, and the new destination must resolve immediately, for every context-sensitive command.
- **Preconditions:** Two collections in the same server (so the "exactly one collection" convenience fallback can't mask a failure); collection A moved via Test 3.4b, from its original location to a new thread.
- **Steps:**
  1. From collection A's *original* (pre-move) location, run `/add`, `/list`, `/vote start`, and `/stats type:Collection`; confirm none of them resolve to collection A there anymore (each should show its usual ambiguous/no-match picker or message, since collection B still exists too).
  2. From collection A's *new* thread, run the same four commands; confirm all four resolve to collection A immediately, with no extra step.
  3. Restart the bot, then repeat step 2.
  4. Run `/database list` and `/config` -> Collections; confirm both show collection A's *current* (post-move) channel, not its original one.
  5. Create a brand-new collection C using collection A's *original*, now-freed location as its destination; confirm this succeeds (the old location is genuinely free for reuse, not permanently reserved).
- **Expected Result:** Exactly one location resolves to collection A at any given time -- its current destination -- both before and after a restart; `/database list`/`/config` never show a stale channel; a freed-up former destination can be reused by a different collection.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4c `/database manage`

- **Objective:** Confirm the guided management workflow is the sole entry point for moving, editing, backing up, resetting, and removing a collection (Release UX & Command Surface Cleanup), ordered administrative actions first and destructive actions last, and that Restore Collection correctly hands off to the one action that must remain its own command.
- **Preconditions:** At least one collection already exists.
- **Steps:**
  1. Run `/database manage`; confirm the same collection picker used elsewhere in `/database` appears.
  2. Choose a collection; confirm a management menu appears offering exactly, in this order: **Edit Collection**, **Backup Collection**, **Move Collection**, **Restore Collection**, **Refresh IMDb Metadata**, **Recover Missing IMDb Links**, **Reset Collection**, **Remove Collection**, **Cancel** -- with Reset Collection and Remove Collection visually distinct (red/danger-styled) from the six actions above them, and Refresh IMDb Metadata/Recover Missing IMDb Links both styled like the other administrative (non-destructive) actions.
  3. Choose **Move Collection**; confirm it launches the destination-choice screen (three thread-only options, same order as `/database add`) and completes the move.
  4. Return to `/database manage` and choose **Edit Collection**; confirm it shows the same settings menu `/config` -> Collections shows for that database, and that its **Back** button returns to the `/database manage` management menu (not to `/config`'s picker).
  5. Choose **Backup Collection**; confirm a backup file is produced.
  6. Choose **Restore Collection**; confirm it points the user at running `/database restore` directly (a modal/component interaction cannot carry a file upload, so this cannot be button-driven) rather than silently failing.
  7. Choose **Reset Collection**; confirm the same RESET-confirmation flow and behavior documented in [Backup & Recovery](05-Administration.md#9-backup--recovery).
  8. Choose **Remove Collection**; confirm the collection is deactivated.
  9. Choose **Cancel** at the management menu; confirm the message states no changes were made and nothing is altered.
  10. Confirm `/database move`, `/database backup`, `/database reset`, and `/database remove` do **not** exist as slash commands at all -- typing `/database ` in Discord's command picker offers only **add**, **list**, **health**, **manage**, and **restore**.
- **Expected Result:** `/database manage` is the only way to move, edit, back up, reset, or remove a collection -- no behavior was lost, only the redundant standalone commands; its action menu is ordered administrative-then-destructive with destructive actions visually distinct; `/database restore` is the one action that remains a direct command, for the documented file-upload reason.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4c-1 IMDb Metadata Refresh (`/database manage`)

- **Objective:** Confirm the IMDb Metadata Refresh workflow's scope selection, confirmation, execution, per-suggestion merge behavior, post-sync, progress, final summary, cross-guild isolation, and idempotency all work as documented.
- **Preconditions:** WASH Crew role; a server with at least two active collections, each with a mix of suggestions -- some with a valid, resolvable IMDb link (including at least one pair sharing the identical IMDb link within the same collection, and one pair sharing an IMDb link across two different collections), some with no IMDb link at all, and at least one whose stored metadata is already fully up to date. A second Discord server this same WASH process also serves, with its own collection and at least one suggestion.
- **Steps:**
  1. Run `/database manage`, choose a collection, choose **Refresh IMDb Metadata**; confirm a screen offering **Refresh This Collection**, **Refresh All Collections**, and **Back** -- both scope options shown even if the server has only one active collection.
  2. Choose **Refresh This Collection**; confirm a confirmation screen naming that one collection, the number of collections (1), the number of suggestions eligible for refresh vs. the total considered, a statement that IMDb/OMDb requests will be made, a statement that existing WASH history/statuses are never changed, and a note that this may take time. Confirm no IMDb/OMDb request has been made yet (check logs/network if possible).
  3. Click **Back**; confirm it returns to the scope-choice screen (not the collection's management menu). Click **Cancel** from the confirmation screen; confirm a "cancelled, no changes were made" message and that nothing changed.
  4. Re-enter and choose **Refresh All Collections**; confirm the confirmation screen's collection count matches only this server's *active* collections, and that no collection from the second Discord server appears anywhere on this screen.
  5. Click **Start Refresh**; confirm an immediate acknowledgment that processing has started, followed by periodic (not one-per-suggestion) progress updates showing collections/suggestions processed and running Refreshed/Unchanged/Skipped/Failed/posts-updated counts.
  6. Once complete, confirm the final ephemeral summary shows correct overall totals, and (for the All Collections run) a per-collection breakdown.
  7. Confirm every suggestion that had a valid IMDb link and actually-changed metadata is now updated (check `/list` or the suggestion directly) with fields such as cast, IMDb rating, and MPAA/content rating newly populated where they were previously blank, while its reference ID, original suggester, suggestion date, collection, status, and any vote/watch history are all unchanged.
  8. Confirm the two suggestions sharing one IMDb link within the same collection were both evaluated (one refreshed, and confirm the second is handled gracefully -- either also refreshed if titles don't collide, or reported as Failed without corrupting the first) and that only one IMDb/OMDb request total was made for that shared link (check logs). Confirm the two suggestions sharing an IMDb link *across* two different collections both refresh successfully from a single shared lookup.
  9. Confirm a suggestion with no IMDb link at all is reported as Skipped and is completely untouched.
  10. Confirm the suggestion whose metadata was already fully up to date is reported as Unchanged, and that its public post was not re-edited (if this is separately observable, e.g. via a "last edited" indicator).
  11. Confirm each refreshed suggestion's own public confirmation post was updated in place (poster/runtime/genres/IMDb rating/content rating/director/plot, wherever shown) -- not a new post, and its Status field is unchanged.
  12. Temporarily make a refreshed suggestion's channel inaccessible to WASH (or delete its post) before running the refresh again on it; confirm the metadata update itself still succeeds and is reported as Refreshed, with the post-sync failure counted and shown separately in the summary -- the metadata change is not rolled back.
  13. Run the exact same refresh again (same scope); confirm every previously-refreshed suggestion now reports Unchanged, no suggestion is duplicated, and no new post is created anywhere.
  14. As a non-WASH-Crew member, confirm `/database manage` (and therefore this whole workflow) is unreachable.
  15. With a collection containing several suggestions whose posts all live in the *same* channel, run a refresh and watch the bot's log for `PATCH /channels/{id}/messages/{id}` 429 responses -- confirm they're rare-to-absent compared to before pacing was added, and that the operation still completes with correct totals (an occasional 429 with an automatic retry is fine and doesn't affect the result).
- **Expected Result:** Both scopes work as documented; Refresh All Collections never includes another guild's collections or an inactive collection; nothing external happens before Start Refresh; progress and the final summary are accurate and never flood Discord with one edit per suggestion; suggestion-post edits to the same channel are paced to avoid bursts, significantly reducing Discord 429 responses without changing the final counts; a shared IMDb link is looked up once and applied to every matching suggestion; missing IMDb links are skipped, not guessed; every WASH-owned field (status, history, votes, Discord references) is preserved; post-sync failures are recorded separately and never roll back an already-persisted metadata update; the operation is safely rerunnable and fully idempotent.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4c-2 IMDb Metadata Recovery (`/database manage`)

- **Objective:** Confirm the IMDb Metadata Recovery workflow's scope selection, confirmation, discovery, OMDb search/matching (single match, multiple matches, no match, year-mismatch fallback), Crew Review (Accept/Skip/Search Again/Cancel Recovery), save-then-refresh hand-off, post-sync, progress, final summary, cross-guild isolation, and idempotency all work as documented -- and that it stays entirely separate from IMDb Metadata Refresh.
- **Preconditions:** WASH Crew role; a server with at least one active collection containing: a suggestion with no IMDb link at all whose title/year has exactly one clear OMDb match, a suggestion with no IMDb link whose title matches multiple OMDb results, a suggestion with no IMDb link whose title matches nothing, a suggestion with no IMDb link whose stored year doesn't match any OMDb result for that title (but the title alone does), and a suggestion that already has a valid IMDb link. A second Discord server this same WASH process also serves, with its own collection and at least one suggestion missing an IMDb link.
- **Steps:**
  1. Run `/database manage`, choose a collection, choose **Recover Missing IMDb Links**; confirm a screen offering **Recover This Collection**, **Recover All Collections**, and **Back**, and that it reads as a distinct workflow from Refresh IMDb Metadata (different heading/description).
  2. Choose **Recover This Collection**; confirm a confirmation screen naming the scope, collection count, how many suggestions are missing a usable IMDb link vs. the total considered, a statement that OMDb searches will be made, a statement that every match requires explicit approval and an existing identifier is never overwritten, and that WASH history/statuses are never changed. Confirm no OMDb request has been made yet.
  3. Click **Back**; confirm it returns to the scope-choice screen. Click **Cancel** from the confirmation screen; confirm a "cancelled, no changes were made" message.
  4. Choose **Recover All Collections**; confirm the confirmation screen's collection count matches only this server's *active* collections, and no collection from the second Discord server appears anywhere on this screen. Confirm the already-linked suggestion is never counted as missing.
  5. Click **Start Recovery**; confirm an immediate acknowledgment, then the first suggestion's search screen appears showing a "Recovering N / M" progress line.
  6. For the suggestion with exactly one clear match: confirm it's shown as a single proposed match (title, year, type) with **Accept**, **Skip**, **Search Again**, and **Cancel Recovery**. Click **Accept**; confirm the IMDb identifier is saved, the suggestion's metadata (cast, IMDb rating, MPAA rating, runtime, genres, poster, plot) is populated immediately, and its existing public post is updated in place (not a new post).
  7. For the suggestion with multiple matches: confirm a selection list appears with enough per-option detail (title, year, type) to distinguish them, plus Skip/Search Again/Cancel Recovery. Select one; confirm it lands on the same single-match confirmation screen (Accept still required, nothing saved yet), then Accept it.
  8. For the suggestion with no matches at all: confirm a "no matches found" screen with only Search Again/Skip/Cancel Recovery (no Accept). Use **Search Again**, type a different title (with and without a parenthesized year, e.g. both `Title 1999` and `Title (1999)`), and confirm it re-searches and shows results for the new query. Skip it.
  9. For the suggestion whose stored year doesn't match any result: confirm WASH automatically falls back to a title-only search and visibly flags that the year didn't match rather than silently picking a result.
  10. Confirm the already-linked suggestion from setup was never shown during this run at all.
  11. Re-run Recover Missing IMDb Links on the same collection; confirm the Skipped suggestion is offered again, the Accepted suggestions are correctly excluded (no longer missing a link), and nothing is duplicated.
  12. Start another recovery run and click **Cancel Recovery** partway through; confirm the final summary reports every suggestion not yet reached as Cancelled (not Skipped), and that running recovery again afterward picks up exactly those suggestions.
  13. Temporarily make an accepted suggestion's channel inaccessible to WASH (or delete its post) before accepting a match for it; confirm the identifier and metadata are still saved and reported as Matched, with the post-sync failure counted separately in the summary -- the metadata change is not rolled back.
  14. Confirm the final ephemeral summary shows correct overall totals (Matched/Skipped/Failed/Cancelled/posts updated/post-sync failures), plus a per-collection breakdown for the All Collections run.
  15. As a non-WASH-Crew member, confirm `/database manage` (and therefore this whole workflow) is unreachable.
- **Expected Result:** Both scopes work as documented; Recover All Collections never includes another guild's collections or an inactive collection; nothing external happens before Start Recovery; a suggestion with an existing IMDb link is never offered and never overwritten; every proposed match requires explicit Crew approval before anything is saved; Search Again supports a manually typed title and/or year; a year mismatch is flagged, never silently resolved; Skip never permanently suppresses a suggestion; Cancel Recovery stops the run and marks the remainder Cancelled; accepting a match immediately reuses Refresh IMDb Metadata's own save-and-post-sync logic; every WASH-owned field is preserved; the operation is safely rerunnable and fully idempotent.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4d `/help`'s simplified Collections section

- **Objective:** Confirm `/help`'s Collections section reflects the actual, simplified command surface -- not just a curated subset of a larger set of commands that still exist.
- **Preconditions:** WASH Crew role configured.
- **Steps:**
  1. Run `/help` as WASH Crew; find the Collections section.
  2. Confirm it lists exactly `/database add`, `/database list`, `/database health`, and `/database manage` -- not `/database move`, `/database backup`, `/database restore`, `/database reset`, or `/database remove` individually.
  3. Confirm `/database manage`'s summary mentions move, edit, back up, restore, refresh IMDb metadata, recover missing IMDb links, reset, and remove.
  4. Confirm a note appears pointing at additional shortcuts (i.e. `/database restore`) under `/database` and the Command Reference.
  5. Confirm `/database add`, `/database list`, `/database health`, `/database manage`, and `/database restore` all still run correctly, and that no other `/database` subcommand exists to run (see 3.4/3.4b/3.4c above).
- **Expected Result:** `/help` accurately reflects the real, simplified command surface -- it is not merely hiding commands that still exist elsewhere.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4e `/database health` (Rotation & Collection Health)

- **Objective:** Confirm `/database health` reports an accurate, reconciled eligibility breakdown for a collection, resolves its collection the same way `/list` does, and never itself changes any state.
- **Preconditions:** A collection with a mix of Eligible, In an Active Vote, Vote Winner, and Retired suggestions (run a vote round or two, and retire at least one suggestion, if needed); a second collection in the same server.
- **Steps:**
  1. From inside the collection's own thread, run `/database health`; confirm it resolves to that collection automatically with no picker.
  2. Confirm the report shows: Collection Name, Total Watch Items, Active Watch Items, Eligible for Voting, In an Active Vote, Vote Winners, Retired, Watched, Configured Candidate Count, a **Next Vote** status, and a **Low Pool Status**. Confirm Eligible for Voting and In an Active Vote are shown visually nested/indented beneath Active Watch Items.
  3. Confirm the numbers reconcile: Active Watch Items = Eligible for Voting + In an Active Vote, and Total = Active Watch Items + Vote Winners + Retired + Watched.
  4. Click **Switch Collection** and confirm it shows the report for the other collection instead, in place.
  5. Run `/database health` from a channel not tied to either collection; confirm WASH shows a picker instead of guessing.
  6. Immediately after, run `/list status:Eligible for Voting` and separately `/vote start` against the same collection; confirm the eligible count reported by `/database health` matches exactly what `/list` shows and what `/vote start` actually nominates from.
  7. Run `/database health` again right after step 6; confirm nothing about the collection changed as a result of having checked health.
- **Expected Result:** `/database health` never disagrees with `/list`/`/vote start`'s own eligibility count; its collection selection matches `/list`'s (auto-resolve in thread, Switch Collection button, picker when ambiguous); checking health is always side-effect-free.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.5 Contextual resolution with multiple databases configured

- **Objective:** Confirm behavior is well-defined when more than one suggestion database exists -- WASH resolves the database from context (channel/thread), never from a server-wide "active" pointer.
- **Preconditions:** At least two active suggestion databases in the same server, each tied to a different channel.
- **Steps:**
  1. Run `/add`, `/list`, `/vote start`, or `/stats type:Database` inside one database's configured channel or thread; confirm WASH uses that database automatically, with no prompt.
  2. Run the same command in a channel not tied to either database; confirm WASH asks "Which collection would you like to use?" with a picker listing both instead of guessing.
  3. Run `/config` -> **Collections**; confirm both collections are listed and each is directly, independently editable (destinations and nominee selection) -- neither is reported as "Invalid" for being simultaneously active.
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
- **Preconditions:** A suggestion database whose configured post destination is a public thread (change it via `/config` -> Collections -> the collection -> Suggestion Destination, if the database's original channel differs).
- **Steps:**
  1. Confirm `/config` reports the thread as the configured Suggestion Post Destination.
  2. Run `/add` from inside that thread.
  3. Run `/add` from the database's original channel too.
- **Expected Result:** Both locations resolve to the same database with no "no suggestion database configured" error; the confirmation post appears in the thread.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.6 `/list` -- Active Watch Items (default)

- **Objective:** Confirm the default list view mixes Eligible and In an Active Vote items with a summary line and per-item status emoji.
- **Preconditions:** A collection with an open voting round, so some suggestions are Eligible and some are In an Active Vote; at least one entry with a known original-post link.
- **Steps:**
  1. Run `/list` (default `status:Active Watch Items`).
- **Expected Result:** Ephemeral by default. A summary line appears before the list, e.g. "🟢 Eligible for Voting: 2" then "🗳️ In an Active Vote: 3", using the actual counts. Every entry leads with its status emoji (🟢 or 🗳️), followed by the title and year exactly once (never doubled, e.g. not `(2004) (2004)`), then `| [Original Suggestion](link)` only when a post link exists. No reference number or IMDb link appears. No embed/link-preview card is shown. Long lists page with Previous/Next.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.6b `/list` -- Eligible for Voting and All Watch Items

- **Objective:** Confirm the Eligible for Voting filter shows only the eligible subset, and All Watch Items shows every status together.
- **Preconditions:** Same collection as Test 4.6, plus at least one Vote Winner and one Retired suggestion.
- **Steps:**
  1. Run `/list status:Eligible for Voting`; confirm it shows only 🟢 entries (no In an Active Vote, Vote Winner, or Retired items).
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
- **Preconditions:** A suggestion manually set to Vote Winner via `/suggestion edit`'s Change Status action (which does not record a win date), rather than through an actual completed vote.
- **Steps:**
  1. Run `/list status:Vote Winners` and separately `/suggestion edit` against that suggestion.
- **Expected Result:** Both views show 🏆 Vote Winner with no `Won:` line at all -- never a blank date, a placeholder, or an error.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.7b `/list status:In an Active Vote`, thread auto-resolve, and Switch Collection

- **Objective:** Confirm the In an Active Vote filter, `/list`'s thread-context auto-resolve, and its Switch Collection button all work, and that Eligible for Voting/In an Active Vote never disagree with `/vote start`.
- **Preconditions:** A collection with an open voting round (so some suggestions are In an Active Vote and some remain Eligible); a second collection in the same server.
- **Steps:**
  1. Run `/list status:In an Active Vote`; confirm it shows exactly the suggestions currently nominated in the open round, each leading with 🗳️.
  2. From inside the collection's own thread, run `/list` with no other context; confirm it automatically uses that collection with no picker.
  3. Click **Switch Collection**; confirm it shows the other collection's list in place, without re-running the command.
  4. Run `/list status:Eligible for Voting` and separately start `/vote start`; confirm the eligible count and the actual nominee pool never disagree.
  5. Run `/list status:Vote Winners` (or `Retired`) on a collection that has never had a vote; confirm this never changes any state (checking a terminal-status filter alone must never mutate anything).
- **Expected Result:** In an Active Vote lists exactly the expected suggestions; thread auto-resolve and Switch Collection both work as described; Eligible for Voting always matches what `/vote start` would actually nominate from; Vote Winners/Retired never trigger any side effect; `/list` is always read-only.
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

### 4.8b `/random watch` -- true-random discovery, filters, and session behavior

- **Objective:** Confirm `/random watch` chooses uniformly at random from the collection's eligible pool, its optional filters (Genre, IMDb Rating, MPAA Rating, Actor, Member) combine correctly and reuse the same shared filter-menu architecture as `/vote start`'s Custom Vote Filters, and it never changes any suggestion or vote state.
- **Preconditions:** A collection with several `Available` suggestions across at least two members, two genres, varied IMDb ratings, varied MPAA ratings, and varied cast lists, plus at least one suggestion each in In an Active Vote, Pending Crew Review, Vote Winner, Watched, and Retired.
- **Steps:**
  1. With no collections configured in the guild, run `/random watch`; confirm a clear "create a collection first" message, not a generic error.
  2. With exactly one collection, run `/random watch`; confirm it's used automatically and named on screen, with no picker.
  3. With more than one collection and no unambiguous channel context, run `/random watch`; confirm a picker is shown, consistent with `/list`'s own picker.
  4. Press **Pick Random Item** repeatedly (at least ~15 times); confirm only `Available` suggestions are ever returned -- never one currently In an Active Vote, Pending Crew Review, a Vote Winner, Watched, or Retired.
  5. Press **Pick Again** several times in a row; confirm each press performs a new draw and that picking the same item twice in a row is not blocked (no hidden no-repeat state).
  6. Press **Add Filters**; confirm the filter menu's category dropdown lists Genre, IMDb Rating, MPAA Rating, Actor, and Member, always in that fixed order, each labeled with its current value. Open Member, choose a specific member, and confirm only that member's eligible suggestions are ever returned by **Pick Random Item**; click **Clear Filter** and confirm it resets to Any Member and the full pool returns.
  7. Open Genre, choose a specific genre, and confirm only suggestions tagged with that genre are ever returned, and that the genre options show eligible counts. Combine the member and genre filters together and confirm the result satisfies both.
  8. Narrow the filters until no suggestion matches; confirm a clear message naming the collection and the active filter(s), with a way to change or clear filters or the collection -- not a generic error.
  9. From a result, press **Change Filters**; confirm the collection stays the same and the current filters are shown. Press **Change Collection**; confirm it returns to the collection picker and that filters from the previous collection do not carry over (including genre and MPAA rating options, which should rebuild for the new collection).
  10. Confirm the result names the collection, states the pick was random, shows the item's usual details (title, year, poster, genre, IMDb link, original suggester) without duplicating the IMDb URL as a separate line, and includes a working **View Original Suggestion** link when the suggestion has a linkable original post (omitted when it doesn't).
  11. After several picks, confirm via `/list` and `/database health` that no suggestion's status changed and no voting round was created.
- **Expected Result:** Selection is always uniformly random over the eligible pool with no Favor New/Older Additions weighting; filters narrow correctly, combine correctly, and never persist beyond the session; collection/filter selection and result presentation match the behavior described above; the command never mutates any suggestion or vote state.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.8c `/random watch` -- public result, private setup, member validation, and requester-only controls

- **Objective:** Confirm the setup/filter screens stay private (ephemeral), the actual result is posted publicly once found, only the member who ran `/random watch` can use its buttons, and the expanded member-filter validation rules (server owner/WASH Crew/Watch Party member, all requiring at least one eligible suggestion) behave correctly with a clearly visible warning that never silently falls back to Any Member.
- **Preconditions:** A collection with eligible suggestions; a test member who is none of server owner/WASH Crew/Watch Party member; a test member who holds only the WASH Crew role (not Watch Party); the server owner account, or an account with `Manage Server`-equivalent visibility into who the owner is; a second, unrelated Discord account to test requester-only enforcement.
- **Steps:**
  1. Run `/random watch` and confirm every screen up through pressing **Pick Random Item** (including **Add Filters** and any collection picker) is only visible to you (ephemeral) -- no other member in the channel can see it.
  2. Press **Pick Random Item** until an item is found; confirm the result posts as a normal, publicly visible message in the channel (not ephemeral), and that the private screen that triggered it is replaced by a brief "picked ... see the public post below" note visible only to you.
  3. As a second, different member, attempt to press **Pick Again**, **Change Filters**, or **Change Collection** on that public result; confirm each is rejected with "Only the person who ran this command can use these controls." and nothing changes.
  4. As the original requester, confirm all three buttons work normally on that same public result.
  5. Press **Change Filters** on the public result; confirm it opens a brand-new *private* message (not an edit of the public one) and that the public result message is untouched. Do the same for **Change Collection**.
  6. From that private follow-up, press **Pick Random Item** again; confirm a new public result is posted (a second public message, not an edit of the first).
  7. In **Add Filters**, open Member and select someone who is on none of server owner/WASH Crew/Watch Party -- confirm a highly visible warning appears (leading ⚠️, the member's name, and the specific reason) and that **Pick Random Item** becomes disabled/unusable until resolved.
  8. Select a valid member (any of server owner/WASH Crew/Watch Party) who has zero eligible suggestions in the collection -- confirm a distinct warning naming the collection (e.g. "has no eligible suggestions in \"Movie Suggestions\"") and that **Pick Random Item** stays disabled.
  9. Select a member who is only WASH Crew (not Watch Party) with at least one eligible suggestion -- confirm they're accepted and the eligible count is shown. Repeat for a member who is only the server owner, and again for a bot account that holds the Watch Party role and has an eligible suggestion -- confirm all three are accepted, and that a bot with none of the three roles is rejected exactly like a non-member human.
  10. After an invalid selection, clear the member filter -- either via the select's own native "clear" gesture or by clicking the **Clear Filter** button on the Member edit screen; confirm either path makes the warning disappear immediately and **Pick Random Item** re-enables.
  11. Confirm the **Current Filters** block always lists Genre, IMDb Rating, MPAA Rating, Actor, and Member in that fixed order (showing "Any X" for each when inactive), as a dot-leader-aligned block (e.g. "Member ......... Any Member"), matching `/vote start`'s Customize This Vote screen's own layout and wording exactly.
- **Expected Result:** Setup/filter screens are always private; a found result is always public; Pick Again/Change Filters/Change Collection work only for the original requester and are cleanly rejected for anyone else; Change Filters/Change Collection from a public result always open a new private message rather than editing the public one; the expanded member validation (owner/WASH Crew/Watch Party, each requiring an eligible suggestion) is enforced with a clear, immediately-appearing-and-clearing warning that blocks progress the whole time it's shown; bots are judged by the same rules as anyone else.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.8d `/random watch` and `/vote start` -- shared IMDb Rating, MPAA Rating, and Actor filters

- **Objective:** Confirm the three newer shared filters (IMDb Rating, MPAA Rating, Actor) work identically through both `/random watch`'s Add Filters and `/vote start`'s Customize This Vote -> Edit Filters, including validation, resets, and missing-metadata handling.
- **Preconditions:** A collection with suggestions covering a spread of IMDb ratings (including some with no rating recorded), several different MPAA/content ratings (including one "Not Rated" and one "Unrated" title, and some with no rating recorded), and overlapping/distinct cast lists (including some suggestions with no cast recorded and at least one actor appearing in more than one suggestion).
- **Steps:**
  1. Open the filter menu (either flow) and select **IMDb Rating**; click **Set Rating Range**; enter only a minimum (e.g. 7.0) and confirm only suggestions rated 7.0 or higher are returned/counted; reset to only a maximum (e.g. 5.9) and confirm only suggestions rated 5.9 or lower are returned; set both a minimum and maximum (e.g. 6.0-8.0) and confirm both boundary values themselves match (inclusive). Confirm the modal's two fields are labeled "Minimum IMDb Rating"/"Maximum IMDb Rating".
  2. Enter a non-numeric value, a value outside 0.0-10.0, or a minimum greater than the maximum; confirm each is rejected with a clear, specific message and the modal/screen stays open to correct it, without silently clearing or accepting the bad value.
  3. Click **Clear Filter** on the IMDb Rating edit screen; confirm the filter clears immediately, the screen returns to the filter menu, the Current Filters summary shows "Any IMDb Rating" right away, and the full pool (including suggestions with no recorded rating) is eligible again.
  4. Select **MPAA Rating**; confirm the dropdown only lists ratings actually present in the collection's eligible pool (each showing an eligible count, no inline "Any" option mixed in), and that "Not Rated" and "Unrated" appear as two distinct options when both are present. Choose one rating and confirm only matching suggestions (case-insensitive) are returned; confirm a suggestion with no recorded rating never matches while a rating is selected. Confirm this editor has the same three-part shape as Genre/Member (picker, **Clear Filter**, **Back**).
  5. Select **Actor**; click **Search Actor**; search part of a name that matches exactly one cast member and confirm it applies immediately without an extra confirmation step. Search a fragment matching more than one actor and confirm a picker lists every match (with counts) for you to choose from; select one and confirm it applies. Search something matching no one and confirm a clear "no actors found" message is shown without leaving the filter flow. Click **Clear Filter** and confirm the filter clears, the summary updates immediately, and you're returned to the filter menu.
  6. Combine all five filters (Genre, IMDb Rating, MPAA Rating, Actor, Member) at once; confirm the result satisfies every active filter simultaneously (an intersection, not a union). Confirm every one of the five editors offers the identically-worded and identically-styled **Clear Filter** button (not five different reset wordings), and that the Current Filters summary reads as a dot-leader-aligned block (e.g. "Genre .......... Sci-Fi") rather than a bulleted "Label: value" list.
  7. In `/vote start`'s Customize This Vote, repeat steps 1-6 through **Edit Filters**; confirm identical wording, validation, and behavior to `/random watch`, and that active filters are shown in **Review This Vote**, the voting post, `/vote status`, and the results announcement after the round completes.
  8. Restart the bot with an active filtered voting round in progress; confirm `/vote status` and the restored voting post still show the correct active filters.
- **Expected Result:** IMDb Rating, MPAA Rating, and Actor behave identically through both flows; validation errors are specific and non-destructive; "Any" resets each filter immediately and independently; a suggestion missing the relevant metadata never matches an active filter but is fully eligible when that filter is Any; all five filters combine as a strict intersection; filter state survives a bot restart for Custom Vote.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.8e `/browse` -- collection browser, shared filters, pagination, and Crew actions

- **Objective:** Confirm `/browse` opens the shared filter menu first (results are never built/shown before **View Results** is clicked), reuses the shared filter engine and eligible pool identically to `/random watch`/Custom Vote, paginates correctly, revalidates filters on collection change (always returning to the filter menu, never straight to results), and that its three WASH-Crew-only actions (Random Pick, Start Vote, Post Publicly) operate on the current filtered results without rebuilding filters or duplicating any existing logic.
- **Preconditions:** WASH Crew role; a server with at least two active collections, one with enough suggestions (40+) to force multiple pages, covering a spread of genres, IMDb ratings, MPAA ratings, and cast members (including some suggestions with no metadata at all); a non-Crew Watch Party member available for permission checks.
- **Steps:**
  1. As a non-Watch-Party member, run `/browse`; confirm it's rejected with WASH's standard permission message.
  2. As a Watch Party member (not WASH Crew) with only one active collection available, run `/browse`; confirm it lands directly on the shared filter menu (no separate collection-choice step), naming the resolved collection, and that no results/matches are shown yet.
  3. With a second active collection created, run `/browse` again; confirm a "Which collection?" picker appears first, and that choosing one leads to the filter menu (still not results) for that collection.
  4. On the filter menu, confirm the same **Current Filters** summary format (dot-leader-aligned) used by `/random watch` and Custom Vote is shown, and that a **View Results** button is present.
  5. Click **View Results** with no filters set; confirm it now builds and shows the paginated results screen, with only **Change Filters**, **Change Collection**, and (when more than one page) **Previous**/**Next** for a non-Crew member -- confirm **🎲 Random Pick**, **🗳️ Start Vote**, and **📢 Post Publicly** are not merely disabled but entirely absent.
  6. Confirm each result line shows title, year, IMDb rating (when known), MPAA rating (when known), genres (when known), who suggested it, its status, and its reference number; confirm a suggestion missing some/all of that metadata still renders without error.
  7. From the results screen, click **Change Filters**; confirm it returns to the identical shared `FilterMenuView` (Genre, IMDb Rating, MPAA Rating, Actor, Member, in that fixed order) with every previously-set filter still shown as active and the same collection still selected. Set one filter and click **View Results**; confirm the results and match count update accordingly and the view is back on page 1.
  8. With the large collection, confirm results paginate (Previous/Next, with a page indicator) rather than truncating, and that Previous/Next never re-applies filters, never resets to page 1, and never rebuilds or reopens the filter menu by themselves.
  9. Set a filter, click View Results, then page to page 2 or later; confirm the filter is still shown as active and still narrows the results (filters persist across pagination).
  10. From the results screen, click **Change Collection**; confirm every active collection is listed (including the current one); switch to the second collection and confirm it returns to the filter menu for that collection (never straight to results), with the eligible pool and dynamic filter options (which genres/MPAA ratings are offered) rebuilt for it. Set a Genre filter present only in the first collection, then Change Collection to the second (where that genre doesn't appear); confirm the Genre filter is cleared but an IMDb Rating range set beforehand is preserved, both visible on the filter menu before clicking View Results again.
  11. Narrow the filters (or switch to an empty collection) and click **View Results** until nothing matches; confirm the paginated results screen never opens -- instead a clear "No suggestions match the current filters" message appears, offering **Change Filters**, **Change Collection**, and **Back** (Back and Change Filters both return to the filter menu), never a dead end.
  12. As WASH Crew, repeat steps 2-6; confirm **🎲 Random Pick**, **🗳️ Start Vote**, and **📢 Post Publicly** are all present on the results screen alongside Change Filters/Change Collection/Previous/Next, and confirm they're still absent from the empty-results message from step 11.
  13. Set an active filter, click View Results, then click **🎲 Random Pick**; confirm the ephemeral browse screen is left completely unchanged (no edit), and a public result is posted in the channel using the exact same presentation `/random watch`'s own result uses (title, year, poster, genre, IMDb link, suggester, reference, "🎲 Picked at random" footer), drawn only from suggestions matching the active filter -- never from the whole collection. Click **Pick Again** on that public result and confirm it still only draws from the same filtered set.
  14. Click **🗳️ Start Vote**; confirm it opens the same "Customize This Vote" screen `/vote start` -> Customize This Vote already shows, with the collection already resolved (no "which collection?" prompt) and the Current Filters summary already showing the exact filter(s) set on the browse screen. Confirm Edit Filters, Vote Settings, Nominee Selection, and Review This Vote all behave identically to reaching this screen from `/vote start` directly, and that starting the vote creates a round using the carried-over filter(s).
  15. Click **📢 Post Publicly**; confirm a brand-new public message appears (the ephemeral browse screen is not converted to public and remains fully usable afterward) showing the collection, active filters, match count, and the current page's suggestions. If multiple pages exist, confirm the public message starts on the same page the browse screen was showing, and has its own independent Previous/Next.
  16. As a different member (including a different WASH Crew member), attempt to use the public post's Previous/Next; confirm only the WASH Crew member who posted it can page through it.
  17. Restart the bot (or simulate a fresh session) and re-run the whole flow once more to confirm nothing here depends on any persisted state surviving a restart (this is a fully ephemeral/session-based feature, not a durable workflow).
- **Expected Result:** `/browse` always opens the shared filter menu first and never builds or shows results until View Results is clicked; it reuses the shared filter menu, eligible pool, Current Filters summary, `/random watch`'s selection/result logic, and Custom Vote's Customize This Vote screen without any duplicated filtering, pagination, or result-formatting logic; non-Crew never sees the three Crew-only actions on either the results or empty-results screen; pagination and filters behave exactly as documented (filters persist across pages/actions, changing filters or collections always returns to the filter menu and then page 1 of results, collection changes clear only filters that no longer apply); empty results never open the paginated screen and always offer a recoverable Change Filters/Change Collection/Back; Random Pick and Start Vote always operate on the current filtered results; Post Publicly never disturbs the ephemeral session and is requester-gated.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.9 `/maintenance repair`

- **Objective:** Confirm the legacy-record repair command runs and reports a summary.
- **Preconditions:** WASH Crew role.
- **Steps:**
  1. Run `/maintenance repair`.
- **Expected Result:** A summary (scanned/repaired/removed/failed/unchanged counts) is returned. Note: this command repairs legacy IMDb-link titles and known malformed records only -- it does not and cannot recover a missing Original Suggestion link for a pre-existing suggestion (a documented limitation, not a defect).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.10 Suggestion rejection ("I Won't Watch") and Crew Review

- **Objective:** Confirm the configurable "I Won't Watch" threshold, the New Review Workflow (Pending Crew Review, Admin Channel notification, Retire/Keep Active/Reset Rejections), `/reject`, `/unreject`, and `/reject`'s Undo Rejection button.
- **Preconditions:** A suggestion with its public confirmation post visible; the collection's "I Won't Watch" enabled with its threshold set to 2 (`/config` -> "I Won't Watch" Settings -> [Collection]); an Admin Channel configured.
- **Steps:**
  1. Click **I Won't Watch** on the confirmation post as one member; confirm the count updates (e.g. "I WON'T WATCH: 1 / 2") and the button state changes.
  2. Click it again as the same member; confirm the rejection is removed (toggle, not additive).
  3. As a Watch Party member (WASH Crew inherit this too, but it is not WASH Crew-only), run `/reject suggestion_id:<id>`; confirm the confirmation includes a link back to the suggestion's original public post and an **Undo Rejection** button.
  4. Click **Undo Rejection**; confirm the rejection is removed, the confirmation message updates to say so, and the suggestion post's rejection count refreshes. Confirm another member cannot use your Undo Rejection button.
  5. Reject again, then run `/unreject suggestion_id:<id>` directly to confirm it still works independently of the button.
  6. As a second distinct member, click **I Won't Watch** to reach the configured threshold (2). Confirm: the suggestion is **not** retired automatically; its status becomes ⚠️ **Pending Crew Review**; it disappears from `/list status:Eligible for Voting`; its "I Won't Watch" button shows the single-line "Review Pending — I WON'T WATCH" and can no longer be clicked; and the configured Admin Channel receives a notification naming the suggestion, its collection, the rejection count, and who voted "I Won't Watch", with **Retire**, **Keep Active**, **Reset Rejections**, and **View Suggestion** buttons.
  7. As a non-WASH-Crew member, attempt to click one of the Crew Review buttons; confirm it's rejected with a permission message.
  8. As WASH Crew, click **View Suggestion**; confirm it links back to the original suggestion post.
  9. As WASH Crew, click **Keep Active**; confirm the suggestion returns to 🟢 Available, its rejection count resets to 0/threshold, its "I Won't Watch" button re-enables, and the Crew Review notification updates to show the outcome with its buttons removed.
  10. Repeat step 6 to reach Pending Crew Review again, then click **Reset Rejections**; confirm the same outcome as Keep Active (Available, rejections cleared, button re-enabled).
  11. Repeat step 6 once more, then click **Retire**; confirm the suggestion's status becomes 🗄️ Retired, it remains visible via `/list status:Retired`, and it can be reactivated through `/add` like any other retired suggestion.
  12. Attempt `/reject` or the button again on an already-Pending-Crew-Review suggestion (before resolving it); confirm it's rejected with a clear "pending WASH Crew review" message and no state change.
- **Expected Result:** Rejections never double-count per member; the Undo Rejection button works exactly once per rejection and gracefully reports "haven't rejected" if clicked after the rejection was already reverted another way; reaching the threshold moves the suggestion to Pending Crew Review rather than retiring it automatically, notifies the Admin Channel, and freezes further rejection changes until WASH Crew resolves it; Retire/Keep Active/Reset Rejections each produce their documented outcome and are WASH-Crew-only; retired items remain visible via `/list status:Retired` and can be reactivated through `/add`.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.10a "I Won't Watch" Settings: dedicated Setup Wizard step and `/config` section

- **Objective:** Confirm "I Won't Watch" has its own dedicated Setup Wizard step (immediately after Voting Defaults) and its own numbered `/config` section, separate from Voting Defaults; confirm Enable/Disable plus threshold (1-10, default 2) are configurable in both, persist correctly, are scoped per collection, and existing collections default to enabled with threshold 2.
- **Preconditions:** WASH Crew role; a server with more than one collection for the multi-collection steps.
- **Steps:**
  1. Run `/setup`; confirm the Voting Defaults step's modal only has candidate count and vote duration fields (no threshold field), and confirm the very next step is titled "I Won't Watch" Settings with **Enable "I Won't Watch" (Recommended)** and **Disable "I Won't Watch"** buttons -- not a free-text field.
  2. Click Enable; confirm a modal opens with only a "Threshold (1-10)" field, prefilled with 2.
  3. Enter a value outside 1-10 (e.g. 0 or 11); confirm a clear validation error and the same Enable/Disable choice is shown again.
  4. Enter a valid value (e.g. 5) and complete setup; confirm the Review screen and completion summary both show "I Won't Watch: Enabled, threshold: 5" as its own line, separate from the Voting Defaults line.
  5. Restart `/setup` for a second, fresh collection and click Disable instead; confirm no modal opens and the wizard advances directly to Reminder Defaults; confirm the completion summary shows "I Won't Watch: Disabled".
  6. Open `/config`; confirm the main numbered menu lists "I Won't Watch" Settings as its own section, positioned immediately after Voting Defaults, with no description text next to it.
  7. With only one collection in the server, open the "I Won't Watch" Settings section; confirm it edits that collection directly (no collection picker) and shows its current Enabled/threshold or Disabled status.
  8. With more than one collection, open the "I Won't Watch" Settings section; confirm it first asks which collection to edit, each option showing that collection's current status, before showing the Enable/Disable screen.
  9. Change one collection's setting (e.g. disable it, or change its threshold to 3) and confirm only that collection changes -- a second collection's own setting is untouched.
  10. Disable a collection that had threshold 7 configured, then re-enable it without entering a new value's worth of attention (skip straight to checking the prefill); confirm the threshold modal pre-fills 7, not the documented default of 2.
  11. Open `/config` -> Collections -> [Collection] -> its per-collection settings menu; confirm it shows "I Won't Watch: Enabled (threshold N)" or "Disabled" for reference, but no longer offers a "Rejection Settings" item to edit it from there (editing only happens from the dedicated section).
  12. For a collection created before this feature existed (or with no explicit override saved), confirm its status reads as Enabled with threshold 2 (the documented defaults) rather than an error or blank value.
- **Expected Result:** "I Won't Watch" Settings is fully separate from Voting Defaults in both Setup and `/config`; Enable/Disable is a button choice, never a free-text field; the threshold is only ever asked for when enabling; both settings are per-collection, validated to 1-10, and disabling never discards a previously configured threshold; existing collections default to enabled with threshold 2 without any migration step.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.10b "I Won't Watch" disabled behavior

- **Objective:** Confirm disabling "I Won't Watch" for a collection hides the button, blocks new rejection tracking, preserves existing rejection history, and that re-enabling never retroactively triggers Pending Crew Review.
- **Preconditions:** WASH Crew role; a collection with "I Won't Watch" enabled, threshold 2; a suggestion in it with one existing rejection (below the threshold).
- **Steps:**
  1. Disable "I Won't Watch" for the collection via `/config`.
  2. Post a new suggestion in that collection; confirm its public post has no **I Won't Watch** button at all (only Mark as Watched), not merely a disabled one.
  3. Attempt `/reject suggestion_id:<id>` against any suggestion in that collection; confirm it's refused with a clear "disabled" message and no state change.
  4. Check the suggestion with the pre-existing rejection from the precondition; confirm its recorded rejection is still present (`/list` or `/suggestion edit` reference) and its status has not changed -- disabling never discards rejection history and never retroactively re-evaluates it against the threshold.
  5. Re-enable "I Won't Watch" for the collection; confirm the suggestion from step 4 is still Available (not Pending Crew Review) purely from the re-enable itself.
  6. Refresh or re-fetch that suggestion's post (e.g. via a bot restart / persistent view restoration); confirm the **I Won't Watch** button now reappears, showing the pre-existing rejection count.
  7. Have a second distinct member reject that same suggestion now that it's re-enabled; confirm this fresh rejection correctly reaches the threshold and moves it to Pending Crew Review as normal.
- **Expected Result:** Disabled means no button and no new tracking, but existing history is preserved untouched; re-enabling only resumes accepting new rejections going forward and never itself triggers Pending Crew Review for an existing below-threshold count.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.11 `/suggestion remove` and `/suggestion edit`

- **Objective:** Confirm WASH Crew administrative status-changing/moving/removal works, including the duplicate re-check on a collection move.
- **Preconditions:** WASH Crew role; at least one suggestion; at least two collections in the server.
- **Steps:**
  1. Run `/suggestion edit`; confirm it shows a read-only summary (title, year, collection, status, IMDb link) plus Change Status, Move to Another Collection, and Cancel -- no title/release year/IMDb link fields to type into. If the suggestion is currently a Vote Winner with a recorded win date, confirm the summary also shows a `Won: <date>` line right below the status.
  2. Choose Change Status; confirm the dropdown offers only Available, Vote Winner (🏆), and Retired (🗄️) (never In an Active Vote or Pending Crew Review, both of which are computed/workflow-driven, not directly settable); pick one and confirm the suggestion's status updates and its public confirmation post's Status field updates in place.
  3. Choose Move to Another Collection; confirm the duplicate check re-runs against the destination collection, and that the suggestion's status is unchanged after the move.
  4. Choose Cancel; confirm nothing changes.
  5. Run `/suggestion remove` with a reference number, then again with an exact title; confirm both resolve correctly and archive (not delete) the record.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 3 exactly; history, stable ID, and (for a move) status are preserved throughout.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.12 Eligible Pool Warning

- **Objective:** Confirm WASH proactively notifies WASH Crew when a collection's eligible pool drops to or below the configured threshold (default: configured candidate count × 5), fires once and suppresses duplicates while it stays at or below the threshold, re-arms once the pool rises back above it, and respects its configured Enabled/Threshold/Destination settings -- for every nominee-selection mode equally.
- **Preconditions:** A collection with a known configured candidate count (e.g. 3, giving a default threshold of 15); Eligible Pool Warning enabled via `/config` (Test 3.2), with a known destination (Admin Channel by default).
- **Steps:**
  1. Reduce the collection's eligible count to or below the threshold (e.g. `/suggestion remove` suggestions, or mark some Vote Winner/Watched), then run `/add` or `/vote start`.
  2. Observe the configured destination (Admin Channel by default, or the Watch Party Home Channel if switched via `/config`) for a message reading "**Eligible Pool Warning** -- \<collection name\>", "Eligible Items Remaining: N", "Warning Threshold: T", and a nudge to use `/add`.
  3. Run `/add` again without raising the pool back above the threshold; confirm the warning does **not** repeat.
  4. Add enough suggestions to raise the eligible count back above the threshold, then run `/add` once more (to trigger re-evaluation); confirm no warning is sent (it's disarming, not firing).
  5. Drop the pool back to or below the threshold and run `/add` again; confirm the warning fires again.
  6. Via `/config`, disable the warning; repeat step 1's conditions; confirm nothing is posted.
  7. Re-enable it and switch its destination to the Watch Party Home Channel; confirm the next warning posts there instead of the Admin Channel.
  8. Repeat steps 1-2 on a Pure Random (or Favor New Additions/Favor Older Additions) collection; confirm the warning fires there too, on the same terms -- no mode is exempt.
- **Expected Result:** The warning fires only when the eligible count is genuinely at or below the threshold, stays silent on repeat checks while it remains there, automatically re-arms once the pool rises back above the threshold, and fires again on the next drop; Enabled/Disabled and Destination are both respected; every nominee-selection mode behaves identically.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.13 New-suggestion immediate eligibility

- **Objective:** Confirm a newly added suggestion is immediately eligible for voting, regardless of nominee-selection mode.
- **Preconditions:** A voting round already open for a collection (so there's an active pool to compare against).
- **Steps:**
  1. `/add` a new suggestion to that collection.
  2. Run `/list status:Eligible for Voting`; confirm the new suggestion appears immediately.
- **Expected Result:** Matches [Administration](05-Administration.md)'s "New suggestion admission" section exactly -- there is no admission delay for any mode.
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

### 5.1b `/vote start` -- Customize This Vote's Nominee Selection Mode override

- **Objective:** Confirm Customize This Vote can override the target collection's Nominee Selection Mode for one round only, without changing the collection's own saved setting, and that the same mode descriptions and visibility explanation from Setup/`/config` appear here too.
- **Preconditions:** A collection configured with **Favor Older Additions** and several eligible suggestions.
- **Steps:**
  1. Run `/vote start` -> **Customize This Vote**; confirm a dropdown appears (Nominee Selection Mode) with the same three options and descriptions as Test 2.8, with **Favor Older Additions** preselected. Confirm a **Continue to Vote Settings** button is present.
  2. Confirm the message body also explains Visible/Blind (same wording as Test 2.7).
  3. Without touching the dropdown, press **Continue to Vote Settings** directly; confirm the familiar candidate count/duration/reminder modal opens (no nominee-selection field in it), submit it, confirm a **Review This Vote** summary appears showing Favor Older Additions, and press **Start Vote**; confirm the round is created using the collection's actual configured mode (Favor Older Additions) -- an untouched dropdown must never silently switch the round to a different mode.
  4. Repeat `/vote start` -> **Customize This Vote**, this time selecting **Pure Random** before pressing **Continue to Vote Settings**; submit the modal, confirm the review screen shows Pure Random, press **Start Vote**, and confirm the round is created successfully using Pure Random for this round only.
  5. Run `/config` -> **Collections** -> this collection -> **Nominee Selection**; confirm it still shows **Favor Older Additions**, unchanged by either override just used.
- **Expected Result:** The override applies to that one round's nominee selection only; the collection's own configured Nominee Selection Mode is never modified by Customize This Vote.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.1c `/vote start` -- Custom Vote Filters: Suggestion Source (one member)

- **Objective:** Confirm Customize This Vote can narrow nominees to one eligible member's suggestions, with correct validation against the expanded member rule (server owner, WASH Crew, or Watch Party member), a highly visible warning and disabled **Continue to Vote Settings** for an invalid selection, live eligible-count feedback, and insufficient-pool handling.
- **Preconditions:** A collection with several eligible suggestions from at least two different Watch Party members, at least one eligible suggestion from a member who has since left the Watch Party role (or a non-member) entirely, and at least one eligible suggestion from a member who is WASH Crew only (not Watch Party) and/or the server owner.
- **Steps:**
  1. Run `/vote start` -> **Customize This Vote**; confirm a **Suggestion Source** dropdown (a Discord member picker) is present alongside Nominee Selection and Vote Visibility, and a **Current Filters** block listing Member/Genre.
  2. Select a current Watch Party member with eligible suggestions; confirm the screen updates to show how many eligible suggestions they have (e.g. "KC has 5 eligible suggestions.") and the Current Filters block updates to show them.
  3. Select the server owner (with an eligible suggestion) and separately a WASH Crew-only member (with an eligible suggestion, no Watch Party role) -- confirm both are accepted the same way a Watch Party member is.
  4. Select a member who satisfies none of server owner/WASH Crew/Watch Party member; confirm a highly visible warning appears (leading ⚠️, the member's name, and the specific reason) and that **Continue to Vote Settings** becomes disabled. Attempt to press it anyway (if still reachable) and confirm it does not proceed.
  5. Select a valid member (any of the three) with zero eligible suggestions; confirm a distinct warning naming the collection and that **Continue to Vote Settings** stays disabled.
  6. Clear the Suggestion Source selection (or leave it untouched); confirm no member filter is applied, any warning disappears immediately, and **Continue to Vote Settings** re-enables.
  7. With a member filter active and a valid selection, continue through the modal to the candidate count with a value higher than that member's eligible count; press **Start Vote** and confirm the round is *not* created, with a message naming the member, their eligible count, the requested count, and how to resolve it (e.g. "KC has 2 eligible suggestions, but this vote requires 3 nominees. Reduce the candidate count or choose another member.").
  8. Repeat with a candidate count at or below that member's eligible count; confirm the round is created, and every resulting nominee was originally submitted by that member.
- **Expected Result:** A valid member is the server owner, a WASH Crew member, or a current Watch Party member with at least one eligible suggestion (any one of the three roles qualifies); an invalid selection is never silently discarded -- it shows a clear warning and blocks Continue until resolved; the eligible count shown matches reality; an insufficient pool blocks round creation with a clear, actionable message before anything is persisted; a successful round's nominees are exclusively that member's suggestions.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.1d `/vote start` -- Custom Vote Filters: Genre, and combining both filters

- **Objective:** Confirm Customize This Vote can narrow nominees to one genre (from already-stored IMDb metadata), that the genre list and eligible counts are accurate, and that Suggestion Source and Genre combine correctly.
- **Preconditions:** A collection with eligible suggestions spanning at least two different genres, including at least one with no genre metadata at all.
- **Steps:**
  1. Run `/vote start` -> **Customize This Vote**; confirm a **Genre** dropdown is present, listing only genres actually represented among eligible suggestions, each showing its own eligible count (e.g. "Horror -- 7 eligible suggestions").
  2. Select a genre; confirm the summary/review screen (after continuing through the modal) shows that genre.
  3. Clear the Genre selection (or leave it untouched); confirm no genre filter is applied and the review screen omits "Genre".
  4. Select both a Suggestion Source and a Genre together; continue to the review screen and confirm both are shown, then press **Start Vote** and confirm every resulting nominee matches both filters (that member's suggestions, tagged with that genre).
  5. Choose a genre/candidate-count combination that leaves fewer eligible suggestions than requested; confirm the round is *not* created, with a message naming the genre, the eligible count, the requested count, and how to resolve it (e.g. "The Horror filter leaves 2 eligible suggestions, but this vote requires 3 nominees. Reduce the candidate count or choose another genre.").
  6. Run `/config` -> **Collections** -> this collection -> **Nominee Selection**, and separately inspect the collection's suggestion rules; confirm neither filter used above was saved anywhere on the collection.
  7. Once a filtered round is open, run `/vote status` and observe the voting post; confirm both show the active filter(s) (e.g. "Suggestion Source: <mention>", "Genre: Comedy"), and that closing the round and viewing the results announcement shows the same filter lines. Confirm an *unused* filter (Any Member/Any Genre) never appears anywhere.
  8. Restart the bot with the filtered round still open; confirm `/vote status` still shows the same filter lines after restart, and the voting post's interactive buttons still work.
- **Expected Result:** The genre list only ever shows genres actually present, with accurate counts; a suggestion with no genre metadata never matches; combining both filters narrows to their intersection; an insufficient combined pool blocks round creation cleanly; neither filter is ever persisted to the collection's configuration; active filters are visible in the review screen, voting post, `/vote status`, and results, survive a restart, and an inactive filter is never shown.
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

### 5.5 Nominee selection modes affect nominee choice

- **Objective:** Confirm each nominee-selection mode visibly changes nomination behavior over multiple rounds.
- **Preconditions:** A database with more eligible suggestions than the candidate count; WASH Crew access to `/config`.
- **Steps:**
  1. Set the database's mode to **Favor New Additions**; add suggestions on different days (or simulate differing suggestion dates); run several rounds; confirm recently-added suggestions are chosen noticeably more often, while older ones remain selectable.
  2. Switch to **Favor Older Additions**; confirm the preference reverses -- the longest-waiting suggestions are chosen noticeably more often.
  3. Switch to **Pure Random**; confirm no exclusion/weighting is applied at all.
- **Expected Result:** Behavior matches [Administration](05-Administration.md)'s "Nominee selection" section for each mode.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.5b Insufficient candidates is reported clearly

- **Objective:** Confirm a collection with fewer eligible suggestions than the requested candidate count reports a clear "not enough eligible suggestions" error rather than silently starting a smaller round.
- **Preconditions:** A collection with exactly 3 suggestions.
- **Steps:**
  1. Run `/vote start` with a candidate count of 2; confirm it succeeds and note which 2 suggestions were nominated.
  2. Let the round complete (or use `/vote edit` -> **End Now**).
  3. Confirm `/list status:Eligible for Voting` shows all non-winning suggestions as 🟢 Available.
  4. Run `/vote start` with a candidate count of 4 (more than the collection's remaining eligible suggestions).
- **Expected Result:** `/vote start` reports a clear "not enough eligible suggestions" error naming the requested and actual counts, and does not create a round.
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
  2. Use **Shorten Vote** -> **1 Hour**; confirm the round's end time moves 1 hour *earlier than its current deadline* (not 1 hour from now), and that the public post and a public notice both reflect the new deadline with a Discord relative timestamp shown. Repeat with **Extend Vote** -> **1 Day** and confirm it moves 1 day *later* than the current deadline. Before choosing a quick pick, confirm the prompt itself explains the Custom option's format: "...Custom... for any other amount of minutes, hours, or days (e.g. 10m, 1h, 7d)."
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
- **Preconditions:** WASH Crew role; a known suggestion ID. `watch_item_id` takes a plain integer, not a `#0007`-style reference -- strip the `#` and leading zeros from a suggestion's Reference field (shown on its confirmation post, or in `/suggestion remove`'s picker) to get the integer.
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

> This section was added during the release-validation review: `/join watch party` and the `/membership` administrative command group are a fully implemented, significant feature area not called out in the original checklist outline. See "Coverage Review" in the delivery notes.

### 7.1 `/join watch party` -- Self-Service mode

- **Objective:** Confirm a member can join (and leave) the Watch Party role directly when Self-Service mode is configured.
- **Preconditions:** Watch Party join mode set to Self-Service (Test 2.2 or `/config`).
- **Steps:**
  1. As a member without the Watch Party role, run `/join watch party`.
  2. Run it again to leave.
- **Expected Result:** The role is granted immediately on the first run, and removed on the second (if `allow_self_leave` is enabled).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 7.2 `/join watch party` -- Manual and Approval-Required modes

- **Objective:** Confirm the other join modes behave as documented.
- **Preconditions:** Join mode set to Manual, then separately to Approval-Required.
- **Steps:**
  1. In Manual mode, run `/join watch party` as a member; confirm it explains that WASH Crew must add them manually (see Test 7.3).
  2. In Approval-Required mode, run `/join watch party`; confirm a request is created and appears in `/membership pending`.
- **Expected Result:** Manual mode never grants the role directly. Approval-Required creates a pending request visible to WASH Crew, and (if an admin channel is configured) posts it there.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 7.3 `/membership` administrative subcommands

- **Objective:** Confirm each WASH Crew membership-administration subcommand works.
- **Preconditions:** WASH Crew role; at least one pending request (Test 7.2) and one existing member.
- **Steps:**
  1. Run `/membership members` -- confirm the current membership list appears.
  2. Run `/membership pending` -- confirm the pending Approval-Required request appears.
  3. Approve or deny it, then run `/membership approved` and `/membership denied` to confirm it moved to the correct list.
  4. Run `/membership add member:<user>` and `/membership remove member:<user>` to manually grant/revoke the role.
  5. Run `/membership search member:<user>` to confirm membership history is reported.
- **Expected Result:** Every subcommand works as named and is restricted to WASH Crew.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 7.4 Discord-Managed join mode

- **Objective:** Confirm WASH correctly defers to an externally managed role.
- **Preconditions:** Join mode set to Discord-Managed.
- **Steps:**
  1. Run `/join watch party` as a member.
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

### 8.3 Suggestion and Database statistics

- **Objective:** Confirm the remaining two statistic types work and respect the public-posting rule.
- **Preconditions:** WASH Crew role; an existing suggestion and database.
- **Steps:**
  1. Run `/stats type:Suggestion suggestion:<reference or title>`.
  2. Run `/stats type:Database`.
  3. Repeat one with `public:true` as WASH Crew, then attempt it as a regular member and confirm it's rejected.
- **Expected Result:** Each type reports the fields documented in [Administration](05-Administration.md) Section 10; public posting for these two types is WASH Crew only.
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

### 9.1 `/maintenance backup`

- **Objective:** Confirm a full manual backup can be created and downloaded.
- **Preconditions:** WASH Crew role; some existing data (suggestions, a completed vote, etc.).
- **Steps:**
  1. Run `/maintenance backup`.
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

### 9.3 `/maintenance restore` -- full restore with confirmation

- **Objective:** Confirm the full-restore flow requires explicit confirmation and never silently overwrites data.
- **Preconditions:** A known-good backup from Test 9.1; some data changed since it was made (so the restore is observable).
- **Steps:**
  1. Run `/maintenance restore backup_filename:<name>` (or upload the `.zip` via `backup_file`).
  2. Review the validation summary WASH shows before doing anything.
  3. Click **Restore**.
  4. Restart the bot (see Test 11.1) and confirm the data matches the backup.
- **Expected Result:** Nothing changes until **Restore** is explicitly clicked; a pre-restore safety backup is created automatically; after restart, live data matches the restored backup exactly.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.4 Backup Collection (`/database manage`) and `/database restore`

- **Objective:** Confirm single-database backup/restore, including both Merge and Replace modes. Backup Collection has no separate top-level command -- it's only reachable through `/database manage`.
- **Preconditions:** At least one suggestion database with several suggestions.
- **Steps:**
  1. Run `/database manage`, choose the database from the picker, then choose **Backup Collection**.
  2. Add a new suggestion to the same database (so Merge has something new to reconcile against).
  3. Run `/database restore mode:Merge` with the backup; confirm existing suggestions are untouched and only non-conflicting ones are added.
  4. Repeat with `mode:Replace`; confirm the database is fully overwritten to match the backup.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 9's Merge/Replace description exactly; a safety backup is made before Replace.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.5 Reset Collection (`/database manage`)

- **Objective:** Confirm the typed-confirmation-gated reset works and only affects the chosen database. Reset Collection has no separate top-level command -- it's only reachable through `/database manage`.
- **Preconditions:** A disposable test database with a few suggestions.
- **Steps:**
  1. Run `/database manage`, choose the database, then choose **Reset Collection**; confirm the shown "would remove" count.
  2. Click **Reset**, then submit anything other than `RESET` in the modal -- confirm nothing changes.
  3. Repeat and type `RESET` exactly.
- **Expected Result:** Only an exact `RESET` proceeds; the database's own record/name/configuration and every other database are untouched; a safety backup is made first.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.6 `/maintenance reset`

- **Objective:** Confirm the full-server reset works and correctly requires `/setup` again afterward.
- **Preconditions:** A disposable test server (this is destructive) with existing configuration and data.
- **Steps:**
  1. Run `/maintenance reset`, review the shown removal count, click **Factory Reset**, type `RESET` exactly.
  2. Run `/setup` afterward.
- **Expected Result:** Every WASH-managed record for the server is removed (server configuration, databases, suggestions, votes, membership requests, scheduled items); a safety backup is made first; `/setup` treats the server as brand new.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.7 `/maintenance import` (cross-instance)

- **Objective:** Confirm importing another instance's backup works in both Merge and Replace modes.
- **Preconditions:** A full backup `.zip` from a *different* WASH instance/server (or a second local test server acting as the source).
- **Steps:**
  1. Run `/maintenance import backup_file:<upload>`.
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
  2. Confirm `/maintenance backup`'s output is what another instance would use as the "export" side of an `/maintenance import`.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 8: `/maintenance backup` doubles as the export mechanism; there is no dedicated export command.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.9 Failure handling

- **Objective:** Confirm corrupt/invalid input fails safely with no data loss.
- **Preconditions:** A deliberately corrupted `.zip` (e.g. truncate a valid backup file, or edit its manifest).
- **Steps:**
  1. Attempt `/maintenance restore` with the corrupted file.
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
- **Expected Result:** A new backup archive appears with `kind: scheduled` in its manifest (distinct from a `kind: manual` backup created via `/maintenance backup`), with no WASH Crew action required.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.11 Automatic backup retention pruning

- **Objective:** Confirm automatic backups beyond the configured retention count are pruned, while manual backups are unaffected.
- **Preconditions:** Automatic backups enabled with a small retention count (e.g. 2 or 3) configured; enough elapsed intervals (Test 9.10) to exceed that count.
- **Steps:**
  1. Let enough scheduled backups accumulate to exceed the configured retention count.
  2. Inspect `data/backups/`.
  3. Create a manual `/maintenance backup` in the same window.
- **Expected Result:** Only the newest N scheduled backups are kept (N = retention count); older scheduled backups are removed automatically. Manual backups are tracked in a separate pool and are never pruned by the automatic-backup retention setting.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.12 Disabling stops future automatic backups without deleting existing ones

- **Objective:** Confirm disabling automatic backups (via `/config` -> Backup Defaults) stops future scheduled backups but leaves existing ones untouched, and that `/maintenance backup` keeps working.
- **Preconditions:** Automatic backups enabled with at least one scheduled backup already created (Test 9.10).
- **Steps:**
  1. Note the existing scheduled backups in `data/backups/`.
  2. Disable automatic backups via `/config` -> Backup Defaults.
  3. Wait past what would have been the next scheduled run.
  4. Run `/maintenance backup` manually.
- **Expected Result:** No new scheduled backup is created after disabling; the previously existing scheduled backups are still present; manual `/maintenance backup` succeeds regardless of the automatic setting.
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
  1. Run `/add`, `/list`, `/random watch`, and `/stats` as this member.
  2. Run `/help` and confirm the shown command list matches what's actually usable.
- **Expected Result:** All four commands work; `/help` accurately reflects this member's permission tier.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 10.2 WASH Crew commands

- **Objective:** Confirm WASH Crew-only commands are usable by WASH Crew and inherit member-level access too.
- **Preconditions:** A member with the WASH Crew role.
- **Steps:**
  1. Run a representative sample: `/vote start`, `/vote status`, `/database add`, `/config`, `/maintenance backup`.
  2. Confirm the same member can also run `/add`/`/list`/`/random watch`/`/stats` (inherited member access).
- **Expected Result:** All succeed; `/help` shows the full WASH Crew command list.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 10.3 Unauthorized users

- **Objective:** Confirm a member with neither role is correctly restricted.
- **Preconditions:** A member with no configured role.
- **Steps:**
  1. Run `/add` and a WASH Crew-only command (e.g. `/vote start`).
- **Expected Result:** `/add` is rejected (member role required); the WASH Crew command is rejected with a message distinguishable from "role not configured" (Test 10.4). `/help`, `/about`, and `/join watch party` remain available to everyone.
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

- **Objective:** Confirm the "I Won't Watch" button on existing confirmation posts still works after a restart.
- **Preconditions:** An existing suggestion confirmation post.
- **Steps:**
  1. Restart the bot.
  2. Click **I Won't Watch** on a pre-restart post.
- **Expected Result:** The click is handled correctly (rejection recorded, button state updates).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 11.5 Crew Review notifications survive restart

- **Objective:** Confirm a Crew Review notification's Retire/Keep Active/Reset Rejections buttons continue working after a restart, without posting a duplicate notification or restoring an already-resolved review.
- **Preconditions:** At least one suggestion currently Pending Crew Review, with its Admin Channel notification still live.
- **Steps:**
  1. Note the existing Crew Review notification's message (and that only one exists for this suggestion).
  2. Restart the bot.
  3. Confirm no additional Crew Review notification was posted for the same suggestion.
  4. Click **Keep Active** (or **Retire**/**Reset Rejections**) on the pre-restart notification.
  5. Confirm it resolves correctly: the suggestion's status updates, the notification message updates to show the outcome with its buttons removed, and the suggestion's own public post's Status field refreshes.
  6. Click **View Suggestion**; confirm it still opens the original suggestion post.
  7. Separately, resolve a different Pending Crew Review suggestion's notification (any action) *before* restarting the bot, then restart. Confirm no notification is restored or re-sent for it, and its notification message is unaffected (still shows the resolved outcome from before the restart).
  8. Separately, manually delete a Crew Review notification message (or its channel) while a suggestion is still Pending Crew Review, then restart the bot. Confirm startup completes normally (a warning is logged, not an error) and no new notification is posted to replace it.
- **Expected Result:** Buttons on a still-open, pre-restart Crew Review notification work exactly as before the restart -- correct suggestion, correct rejection records, correct collection; no duplicate notification is ever created; an already-resolved review is never re-armed; a deleted message/channel is skipped gracefully with a logged warning, never a startup failure.
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
