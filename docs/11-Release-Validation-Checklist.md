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
- Run `python -m unittest discover -s tests -v` (baseline: 2595 tests, 0 failures) before starting manual validation, and again before final sign-off. This checklist verifies real-world behavior the automated suite cannot (Discord UI rendering, actual message delivery, a real bot process restarting) -- it does not replace the automated suite.

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

### 2.4 Save & Finish Later, then resume

- **Objective:** Confirm partial progress can be saved and resumed without loss.
- **Preconditions:** At least two steps completed.
- **Steps:**
  1. On any step, click **Save & Finish Later**.
  2. Confirm the confirmation message explains how to resume.
  3. Run `/setup` again.
  4. Choose **Continue Setup**.
- **Expected Result:** The save confirmation is clear and non-destructive (setup is not marked complete). Running `/setup` again shows a resume prompt naming how many of 9 steps are complete and the current step; **Continue Setup** returns to exactly where you left off with all prior answers intact.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.5 Suggestion database (post destination)

- **Objective:** Confirm a suggestion database can be created and tied to a channel or public thread during setup.
- **Preconditions:** Test 2.1 in progress, reached the Suggestion Database step.
- **Steps:**
  1. Choose **Create New**.
  2. Name the database.
  3. Select a text channel (repeat this test later with a public thread instead, if time allows).
- **Expected Result:** The database is created and associated with the chosen channel/thread; this becomes the suggestion post destination.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.6 Watched Movie Destination

- **Objective:** Confirm the watched-movie destination step accepts a channel or can be skipped.
- **Preconditions:** Test 2.5 passed.
- **Steps:**
  1. Select a destination channel, or click **Skip for Now**.
- **Expected Result:** The choice advances the wizard and is reflected correctly on the Review step.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.7 Voting Defaults: candidate count, duration, visibility

- **Objective:** Confirm the Voting Defaults modal accepts and validates all four fields.
- **Preconditions:** Reached the Voting Defaults step.
- **Steps:**
  1. Open the modal (**Set Voting Defaults**).
  2. Enter a candidate count (try an in-range value, e.g. `4`).
  3. Enter a duration using a natural value (try `4h`, then separately try a bare number like `3`, then `3 days` -- confirm all three are accepted and mean what you'd expect).
  4. Set visibility to `visible` or `blind`.
  5. Submit.
- **Expected Result:** Values in range are accepted; the modal defaults to **24 hours** (shown as "1 day") on a brand-new server. An out-of-range duration (e.g. `31 days` / `0`) is rejected with a clear, specific error naming the 1 hour-30 day range.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 2.8 Candidate selection mode

- **Objective:** Confirm all three candidate-selection modes are selectable by friendly name or raw value.
- **Preconditions:** Test 2.7 in progress (same modal).
- **Steps:**
  1. Enter `Balanced Random` (or `rotation_pool`) in the candidate-selection field; confirm it's accepted.
  2. Repeat later with `Soft Rotation`/`soft_rotation` and `Pure Random`/`infinite_pool`.
- **Expected Result:** All three friendly labels and all three raw values are accepted; **Balanced Random** is the pre-filled default.
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

### 2.10 Reminder Defaults and Backup Defaults

- **Objective:** Confirm the remaining two Voting-adjacent steps save correctly.
- **Preconditions:** Test 2.7 passed.
- **Steps:**
  1. Configure (or disable) the vote-ending reminder and its lead time.
  2. Configure the automatic-backup interval and retention count.
- **Expected Result:** Both steps accept valid values and reject out-of-range ones with clear errors.
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
- **Expected Result:** Every section (roles, join mode, admin channel, suggestion database, watched-movie destination, voting defaults including candidate selection, reminders, backup) is shown accurately, using natural-language duration (e.g. "4 hours" or "3 days", never "72 hours"). Save marks setup complete and shows a final completion summary matching the same values.
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

---

## 3. Configuration

### 3.1 `/config` opens the main menu

- **Objective:** Confirm the always-available configuration menu opens once setup is complete.
- **Preconditions:** Section 2 completed.
- **Steps:**
  1. Run `/config` as WASH Crew.
- **Expected Result:** A menu listing every section (WASH Crew Role, Watch Party Role, Watch Party Join Mode, Admin Channel, Active Suggestion Database, Suggestion Post Destination, Watched Movie Destination, Voting Defaults, Reminder Defaults, Backup Defaults) appears, each showing its current status (Configured/Not configured/Skipped/Invalid).
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
  3. Run `/start_vote` -> **Use Defaults** and confirm the new duration is what's actually used (see Test 5.2).
- **Expected Result:** The value changed in `/config` is the value `/start_vote` actually applies -- no separate/stale value anywhere.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.4 Database management: create, list, remove

- **Objective:** Confirm suggestion database administration works outside the wizard.
- **Preconditions:** WASH Crew role configured.
- **Steps:**
  1. Run `/database_add` with a new name.
  2. Run `/database_list` and confirm the new database appears.
  3. Run `/database_remove`, choose the new database from the picker, and confirm.
- **Expected Result:** Creation succeeds (rejects a duplicate name); the list shows name, Active/Inactive status, and item count; removal applies the documented safety/ownership checks and the database no longer appears afterward.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 3.5 Active database with multiple databases configured

- **Objective:** Confirm behavior is well-defined when more than one suggestion database exists.
- **Preconditions:** At least two active suggestion databases in the same guild.
- **Steps:**
  1. Run `/config`'s Active Suggestion Database section and confirm it reports the ambiguity (rather than silently picking one) or lets you select one explicitly.
  2. Run `/add` in a channel not tied to either database and confirm WASH shows a picker instead of guessing.
- **Expected Result:** WASH never silently guesses between multiple active databases; it always asks or reports the ambiguity clearly.
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

- **Objective:** Confirm definite and possible duplicate warnings behave as documented for both regular members and WASH Crew.
- **Preconditions:** An existing suggestion (e.g. from Test 4.1) with a known release year.
- **Steps:**
  1. As a regular Watch Party member, try to `/add` the exact same title/year again -- confirm it's blocked.
  2. As WASH Crew, try to `/add` the same title with no year -- confirm a possible-duplicate confirmation is offered and reactivation/creation only proceeds after explicit confirmation.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 3's table exactly: active matches always block; archived/watched matches offer WASH Crew reactivation only; possible duplicates require explicit confirmation and never guess.
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
- **Preconditions:** A suggestion database whose configured post destination is a public thread (change it via `/config`'s Suggestion Post Destination section if the database's original channel differs).
- **Steps:**
  1. Confirm `/config` reports the thread as the configured Suggestion Post Destination.
  2. Run `/add` from inside that thread.
  3. Run `/add` from the database's original channel too.
- **Expected Result:** Both locations resolve to the same database with no "no suggestion database configured" error; the confirmation post appears in the thread.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.6 `/list` -- Available

- **Objective:** Confirm the default list view is correct and clean.
- **Preconditions:** At least two active suggestions, at least one with a known original-post link.
- **Steps:**
  1. Run `/list` (default `status:Available`).
- **Expected Result:** Ephemeral by default. Each entry shows the title and year exactly once (never doubled, e.g. not `(2004) (2004)`), followed by `| [Original Suggestion](link)` only when a post link exists. No reference number, status, or IMDb link appears. No embed/link-preview card is shown. Long lists page with Previous/Next.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.7 `/list` -- Watched and Retired

- **Objective:** Confirm the other two status filters work.
- **Preconditions:** At least one retired suggestion (reject one below the retirement threshold in Test 4.10 first, if none exist yet).
- **Steps:**
  1. Run `/list status:Retired`.
  2. Run `/list status:Watched` (expected to be empty -- see Notes).
- **Expected Result:** Retired shows retired/archived items. Watched is expected to be empty in this build -- no code path yet marks an item Watched (a documented, known limitation, not a bug).
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

- **Objective:** Confirm the rejection-threshold/retirement workflow, including `/reject` and `/unreject`.
- **Preconditions:** A suggestion with its public confirmation post visible.
- **Steps:**
  1. Click **I WILL NOT WATCH** on the confirmation post as one member; confirm the count updates and the button state changes.
  2. Click it again as the same member; confirm the rejection is removed (toggle, not additive).
  3. As a Watch Party member (WASH Crew inherit this too, but it is not WASH Crew-only), run `/reject suggestion_id:<id>` and `/unreject suggestion_id:<id>` directly.
  4. Reach the configured rejection threshold and confirm the suggestion is retired (archived) and excluded from further selection.
- **Expected Result:** Rejections never double-count per member; retirement happens automatically at the configured threshold; retired items remain visible via `/list status:Retired` and can be reactivated through `/add`.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.11 `/remove` and `/edit_suggestion`

- **Objective:** Confirm WASH Crew administrative editing/removal works, including the duplicate re-check on edit.
- **Preconditions:** WASH Crew role; at least one suggestion.
- **Steps:**
  1. Run `/edit_suggestion` to change a title; confirm the duplicate check re-runs against the (possibly new) destination database.
  2. Run `/remove` with a reference number, then again with an exact title; confirm both resolve correctly and archive (not delete) the record.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 3 exactly; history and stable ID are preserved through both operations.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 4.12 Low Pool Reminder

- **Objective:** Confirm WASH warns when a database's available-suggestion count runs low.
- **Preconditions:** A database whose active suggestion count is at or just above the configured threshold (default 10).
- **Steps:**
  1. `/add` suggestions (or `/remove` existing ones) until the count crosses the threshold.
  2. Observe the database's suggestion channel (or its separately configured reminder destination) after the triggering `/add`.
- **Expected Result:** A reminder posts naming the remaining count, current rotation completion percentage, and a nudge to use `/add`; it does not repeat more than once per the configured minimum interval regardless of further additions/removals in that window.
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

### 5.1 `/start_vote` -- Use Defaults

- **Objective:** Confirm the default voting flow creates a round using the guild's configured defaults.
- **Preconditions:** At least 2 eligible suggestions; Voting Defaults configured (Section 2/3).
- **Steps:**
  1. Run `/start_vote` -> **Use Defaults**.
- **Expected Result:** A round opens using the configured candidate count, duration, and visibility -- not hardcoded values. The public voting post is WASH's standard embed (yellow accent, no branding) showing visibility, duration/end time, and clean candidate titles (no leading number).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.2 Hour-based duration

- **Objective:** Confirm short, hour-based durations work end to end.
- **Preconditions:** No open round.
- **Steps:**
  1. Run `/start_vote` -> **Customize This Vote**.
  2. Enter a duration of `1h`, submit, and confirm the round's end time is ~1 hour out.
  3. Repeat with `4h`, `12h`, and a bare number (e.g. `3`, meaning 3 days) to confirm all forms are accepted.
- **Expected Result:** All forms are accepted; the resulting end time matches; a value outside 1 hour-30 days (e.g. `0`, `31 weeks`) is rejected with a clear, actionable error.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.3 Visible voting

- **Objective:** Confirm visible-mode standings behave correctly while the round is open.
- **Preconditions:** A round started with `visibility:visible`.
- **Steps:**
  1. Cast votes as two or more members.
  2. Observe the public voting post update after each vote.
  3. Run `/vote_status` as WASH Crew.
- **Expected Result:** The post's standings (progress bar, count, percentage) update after every vote; `/vote_status` shows candidate titles (never an internal suggestion number) with totals and percentages.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.4 Blind voting

- **Objective:** Confirm blind-mode privacy is preserved throughout.
- **Preconditions:** A round started with `visibility:blind`.
- **Steps:**
  1. Cast a vote and read your own ephemeral confirmation.
  2. Have a second member cast a vote; confirm the first member cannot see it anywhere.
  3. Run `/vote_status` while the round is still open.
- **Expected Result:** No standings, counts, or other members' choices are ever revealed while a blind round is open; your own vote confirmation never mentions anyone else's choice; `/vote_status` shows only that the round is open and blind, with total votes cast but no per-candidate breakdown.
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

### 5.7 `/edit_vote` -- change end time, end now, cancel

- **Objective:** Confirm WASH Crew's administrative controls over an in-progress round.
- **Preconditions:** An open round; WASH Crew role.
- **Steps:**
  1. Run `/edit_vote` -> change the end time to a new value; confirm the public post and a public notice both reflect the new deadline.
  2. Start a second round (after the first completes or is cancelled) and use `/edit_vote` -> **End Now**; confirm it closes immediately with correct results.
  3. Start a third round and use `/edit_vote` -> **Cancel Vote**; confirm it's cancelled with no winner announced and the original post's buttons are disabled.
- **Expected Result:** All three actions behave exactly as described, and each updates the original voting post appropriately (new deadline / closed with results / cancelled notice).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.8 Vote completion and winner announcement

- **Objective:** Confirm a round closes automatically at its deadline and announces a winner correctly.
- **Preconditions:** A round started with a short duration (e.g. `1h`, or manually adjusted via `/edit_vote` to close within a few minutes for testing).
- **Steps:**
  1. Wait for the round to reach its deadline (or use **End Now**).
  2. Observe the results announcement.
- **Expected Result:** The original post is updated to a closed record with final standings; a single results announcement is posted with the winner, an "About Tonight's Pick" embed (poster/runtime/rating/genres when known), and a link back to the original post. No duplicate announcement is posted.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 5.9 Tie handling

- **Objective:** Confirm a tied round is announced correctly with no thumbnail ambiguity.
- **Preconditions:** Ability to arrange two candidates with equal votes (e.g. a 2-member test server voting for different candidates in a 2-candidate round).
- **Steps:**
  1. Produce a tie and let the round complete.
- **Expected Result:** Both winners are announced ("It's a tie! Winners: ..."), each gets its own "About Tonight's Pick" embed, and neither embed shows a poster thumbnail (thumbnails are suppressed for any tie).
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

---

## 6. Watch Party Scheduling

### 6.1 Schedule a watch party

- **Objective:** Confirm a watch party can be scheduled from a winning or existing suggestion.
- **Preconditions:** WASH Crew role; a known suggestion ID. `watch_item_id` takes a plain integer, not a `#0007`-style reference -- strip the `#` and leading zeros from a suggestion's Reference field (shown on its confirmation post, or in `/remove`'s picker) to get the integer.
- **Steps:**
  1. Run `/schedule_watch_party watch_item_id:<id> when:"YYYY-MM-DD HH:MM"` with a near-future time.
- **Expected Result:** A confirmation is shown with the scheduled title and time; `/watch_party_status` (Test 6.4) reflects it.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** Flag in Notes if this ID lookup proves confusing in practice -- `/list`'s default view no longer shows reference numbers, so this is the only path today.

### 6.2 Reschedule

- **Objective:** Confirm a scheduled watch party's time can be changed via the watch-party picker.
- **Preconditions:** Test 6.1 passed.
- **Steps:**
  1. Run `/reschedule_watch_party when:"YYYY-MM-DD HH:MM"` with a different time.
  2. Choose the watch party from the picker WASH shows (title and scheduled date/time).
- **Expected Result:** The scheduled time updates; the reminder job (Test 6.5) is rescheduled to match, not duplicated.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 6.3 Cancel

- **Objective:** Confirm a scheduled watch party can be cancelled cleanly via the watch-party picker.
- **Preconditions:** Test 6.1 (or 6.2) passed.
- **Steps:**
  1. Run `/cancel_watch_party`.
  2. Choose the watch party from the picker WASH shows.
- **Expected Result:** The watch party is cancelled; its reminder job no longer fires; `/watch_party_status` no longer reports it as the soonest scheduled item.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 6.4 Watch-party picker and empty state

- **Objective:** Confirm `/reschedule_watch_party` and `/cancel_watch_party` show a picker of currently scheduled watch parties rather than taking a watch party ID directly, and fail clearly when none are scheduled.
- **Preconditions:** No watch party scheduled initially.
- **Steps:**
  1. Run `/reschedule_watch_party` with no watch party scheduled; confirm a clear "no watch parties are currently scheduled" message and no picker appears.
  2. Schedule two or more watch parties (Test 6.1), then run `/reschedule_watch_party` again; confirm a picker lists each by title and scheduled date/time, and selecting one affects only that watch party.
  3. Run `/watch_party_status`.
- **Expected Result:** With nothing scheduled, both commands fail clearly rather than erroring unhelpfully. With one or more scheduled, a picker is always shown to choose the target -- even with only one. `/watch_party_status` reports the soonest-scheduled watch party's title and time, which may not be the one just rescheduled or cancelled if others remain.
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

### 9.4 `/database_backup` and `/database_restore`

- **Objective:** Confirm single-database backup/restore, including both Merge and Replace modes.
- **Preconditions:** At least one suggestion database with several suggestions.
- **Steps:**
  1. Run `/database_backup`, choose the database from the picker.
  2. Add a new suggestion to the same database (so Merge has something new to reconcile against).
  3. Run `/database_restore mode:Merge` with the backup; confirm existing suggestions are untouched and only non-conflicting ones are added.
  4. Repeat with `mode:Replace`; confirm the database is fully overwritten to match the backup.
- **Expected Result:** Matches [Administration](05-Administration.md) Section 9's Merge/Replace description exactly; a safety backup is made before Replace.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 9.5 `/database_reset`

- **Objective:** Confirm the typed-confirmation-gated reset works and only affects the chosen database.
- **Preconditions:** A disposable test database with a few suggestions.
- **Steps:**
  1. Run `/database_reset`, choose the database, confirm the shown "would remove" count.
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
- **Expected Result:** Every WASH-managed record for the server is removed (guild configuration, databases, suggestions, votes, membership requests, scheduled items); a safety backup is made first; `/setup` treats the server as brand new.
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
- **Expected Result:** Only portable data (databases, their suggestions/configuration, vote rounds) is imported; this server's own guild configuration (roles, channels, guild ID) is never changed; ID collisions are reassigned automatically; results are reported once and not persisted.
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
  2. Attempt `/database_restore` with a full (not single-database) backup, and vice versa.
  3. Attempt `/database_restore` with a backup from a different guild.
- **Expected Result:** Each is rejected with a clear, specific message (see the troubleshooting table in [Administration](05-Administration.md) Section 9); live data is never modified by a failed or rejected attempt.
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
  1. Run a representative sample: `/start_vote`, `/vote_status`, `/database_add`, `/config`, `/backup`.
  2. Confirm the same member can also run `/add`/`/list`/`/stats` (inherited member access).
- **Expected Result:** All succeed; `/help` shows the full WASH Crew command list.
- **Result:** [ ] Pass [ ] Fail
- **Notes:** ___________________________

### 10.3 Unauthorized users

- **Objective:** Confirm a member with neither role is correctly restricted.
- **Preconditions:** A member with no configured role.
- **Steps:**
  1. Run `/add` and a WASH Crew-only command (e.g. `/start_vote`).
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
