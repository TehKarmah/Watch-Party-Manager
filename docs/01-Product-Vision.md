# Watch Party Manager

## Product Vision

| Property     | Value                  |
| ------------ | ---------------------- |
| Document     | Product Vision         |
| File         | `01-Product-Vision.md` |
| Version      | 1.0 Draft              |
| Status       | Draft                  |
| Last Updated | July 2026              |
| Authors      | TehKarmah & ChatGPT    |

---

> [!NOTE]
> This document defines the purpose, vision, and guiding principles of Watch Party Manager. Functional behavior is documented in the remaining specification.

---

## Table of Contents

1. Mission Statement
2. Vision
3. Project Goals
4. Non-Goals
5. Target Audience
6. Guiding Principles
7. Success Criteria

---

# 1. Mission Statement

Watch Party Manager (WPM) is a self-hosted Discord bot that helps communities organize recurring watch parties.

Rather than focusing solely on movies, WPM manages **Watch Items**, allowing communities to organize, vote on, schedule, and preserve the history of any shared viewing experience, including movies, television series, documentaries, and future supported media types.

Watch Party Manager automates repetitive administrative work while preserving the traditions and workflows that make each community unique.

The objective is not to change how communities host watch parties, but to provide a flexible framework that adapts to existing traditions.

---

# 2. Vision

Watch Party Manager should feel like another member of the community.

It quietly manages scheduling, voting, reminders, statistics, and historical records while allowing members to focus on discovering great entertainment and enjoying time together.

Whether supporting four friends or hundreds of community members, Watch Party Manager should remain approachable, reliable, configurable, and enjoyable to use.

---

# 3. Project Goals

The primary goals of Watch Party Manager are:

- Reduce repetitive administrative work.
- Preserve the complete history of every Watch Item.
- Support multiple independent Discord communities.
- Allow communities to customize their workflow without modifying source code.
- Provide reliable scheduling and voting automation.
- Keep long-term data portable through backups and exports.
- Scale without requiring architectural redesign.

---

# 4. Non-Goals

The following items are intentionally outside the scope of Version 1.

- Streaming or media playback.
- Voice or music bot functionality.
- AI recommendation engine.
- Automatic banner generation.
- Mobile applications.
- Web application or dashboard.
- Plex, Jellyfin, or Emby synchronization.
- Letterboxd or Trakt synchronization.
- Monetization features.

These features may be considered for future releases but are not required for Version 1.

---

# 5. Target Audience

Watch Party Manager is intended for Discord communities that regularly watch content together, including:

- Movie clubs
- Television watch groups
- Anime communities
- Documentary groups
- Educational organizations
- Gaming communities
- Friends hosting recurring watch parties

---

# 6. Guiding Principles

These principles are intended to guide every future design and implementation decision. When new features are proposed, they should be evaluated against these principles to ensure Watch Party Manager remains consistent with its mission and philosophy.

## Community First

Watch Party Manager should adapt to each community rather than expecting communities to adapt to the software.

Where practical, community policies should be configurable instead of hardcoded.

---

## Automate Repetitive Work

The software should automate administration rather than creativity.

Examples of work suitable for automation include:

- Voting
- Scheduling
- Statistics
- Rotation management
- Discord event creation
- Historical record keeping

Creative activities such as banner design, discussion, and event themes remain community-driven.

---

## Accessibility First

Accessibility is considered during every feature design.

Examples include:

- Clear and intuitive command names.
- Consistent terminology.
- Emojis that reinforce meaning rather than replace text.
- Interfaces that do not rely solely on color.
- Screen reader friendly messaging where supported by Discord.
- Progressive disclosure of advanced functionality.

---

## Progressive Complexity

New users should be able to participate without learning the entire system.

Advanced administration features should remain available without overwhelming casual users.

---

## User Data Belongs to the Community

Communities should always be able to:

- Export their data.
- Back up their database.
- Restore from backups.
- Move between hosting environments.

No community should become dependent upon a particular hosting provider.

---

## Safe by Default

Administrative actions should avoid irreversible changes whenever practical.

Potentially destructive operations should require confirmation and be recorded in the audit log.

---

## Intelligent, not Surprising

Watch Party Manager should explain important decisions instead of silently making them.

Automation should always remain understandable.

---

## Configurable, not Opinionated

Policies should be configurable whenever practical.

Communities should decide how they want Watch Party Manager to operate.

---

## Preserve the Story

Watch Party Manager exists to preserve the history of a community.

A Watch Item is more than a title.

Its history includes:

- Who suggested it.
- How long it waited.
- How many times it appeared in voting.
- When it won.
- How many times it has been watched.

---

# 7. Success Criteria

Version 1 is considered successful when a server administrator can:

1. Install Watch Party Manager.
2. Complete setup using the setup wizard.
3. Configure channels, roles, schedules, and policies.
4. Accept suggestions from community members.
5. Run recurring voting cycles.
6. Automatically schedule watch parties.
7. Preserve watch history and statistics.
8. Export and back up all server data.
9. Recover from unexpected interruptions without data loss.
10. Operate the system for extended periods without direct database maintenance.

---

> [!NOTE]
> This document describes the purpose and philosophy of Watch Party Manager. Functional behavior is defined in the remaining specification documents.
