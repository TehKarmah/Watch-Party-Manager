# Watch Party Manager

## Functional Specification

| Property     | Value                            |
| ------------ | -------------------------------- |
| Document     | Functional Specification         |
| File         | `03-Functional-Specification.md` |
| Version      | 1.0                              |
| Status       | Active                           |
| Last Updated | July 2026                        |
| Authors      | TehKarmah & ChatGPT              |

---

> [!NOTE]
> This document defines the functional behavior of Watch Party Manager. It describes what the system does from the perspective of users and administrators. Database implementation details are documented separately.

---

## Table of Contents

1. Introduction
2. Watch Item Lifecycle
3. Watch Item Suggestions
4. Nominee Selection
5. Voting
6. Scheduling
7. Watch History
8. Statistics
9. Administration Overview

---

# 1. Introduction

The Functional Specification defines the observable behavior of Watch Party Manager.

It describes the complete lifecycle of a Watch Item, from suggestion through one or more viewings, while outlining how the system manages voting, scheduling, history, and statistics.

Where configuration options exist, this document describes the intended behavior rather than implementation details.

---

# 2. Watch Item Lifecycle

Every Watch Item shows one of five statuses:

```text
🟢 Available  ──▶  🗳️ In an Active Vote  ──▶  🏆 Vote Winner  ──▶  ✅ Watched
       ▲                    │                       │
       └────────────────────┘                       ▼
                                              🗄️ Retired
```

- **Available** -- eligible for future voting.
- **In an Active Vote** -- currently a candidate in an open voting round for its collection; computed at display time, not separately stored, so it automatically returns to Available the moment its round closes.
- **Vote Winner** -- won a voting round. WASH knows a suggestion won a vote; it does not yet know the group actually watched it, unless separately confirmed through the watch-history workflow (see Watched, below). Whenever displayed, a Vote Winner with a recorded win date also shows a `Won: <Month D, YYYY>` line; a legacy Vote Winner with none omits it gracefully.
- **Retired** -- archived, whether by `/remove`, an "I WILL NOT WATCH" rejection threshold, or WASH Crew directly setting it via `/edit_suggestion`.
- **Watched** -- explicitly confirmed watched.

A Watch Item may return to In an Active Vote, and from there back to Available, multiple times throughout its lifetime as voting rounds open and close.

The complete history of every viewing is preserved.

---

## Watch Item

A Watch Item represents a single piece of media that may be watched by the community.

Version 1 supports:

- Movies
- Television series

Future versions may support additional media types without redesigning the Watch Item model.

Each Watch Item maintains a permanent history regardless of how many times it is watched.

---

## Watch Item Journey

The Watch Item Journey records the complete history of a Watch Item.

Examples include:

- Original suggester
- Suggestion date
- Number of voting appearances
- Winning vote
- Watch dates
- Number of rewatches

The Journey is never deleted under normal operation.

---

# 3. Watch Item Suggestions

Community members may suggest Watch Items.

A new suggestion becomes eligible for voting immediately -- there is no admission delay.

Duplicate suggestions are automatically detected.

If a Watch Item has already been watched, administrators may choose whether it should become immediately eligible for rewatch or remain retired.

---

# 4. Nominee Selection

The Eligible Pool is every suggestion that is neither currently nominated, a Vote Winner, Watched, nor Retired.

When a vote starts, nominees are drawn from the Eligible Pool using the collection's configured selection mode: Pure Random (no preference or exclusion), Favor New Additions (leans toward recently-suggested items), or Favor Older Additions (leans toward suggestions that have waited the longest). No suggestion is ever permanently excluded by any mode.

Eligible Pool health is monitored to identify a shrinking pool: the Eligible Pool Warning fires once the pool drops to or below a configured threshold, and re-arms once it rises back above it.

---

# 5. Voting

Voting allows community members to select the next Watch Item.

Voting behavior is configurable.

Configuration options include:

- Blind voting
- Number of voting options
- Maximum vote changes
- Automatic vote closing
- Manual vote closing
- Reminder timing

By default, tie votes result in all tied Watch Items being scheduled. This behavior is configurable.

When multiple Watch Items are scheduled from a tie, they are watched in alphabetical order unless an administrator overrides the schedule.

Voting concludes before the current Watch Party begins, allowing the winner announcement and next vote to be posted together.

---

# 6. Scheduling

Scheduling is managed through Recurring Event Series.

Each Event Series defines:

- Schedule
- Voice channel
- Source type
- Event behavior

Supported source types include:

- Vote Winner
- Manual Assignment
- Birthday Pick
- Holiday Pick

Watch Party Manager may automatically create Discord Events when configured to do so.

Manual Event Series support recurring community traditions without affecting normal voting.

Examples include:

- Television nights
- Monthly special events
- Community marathons

Scheduling uses a configurable scheduling timezone while presenting Discord timestamps in each user's local timezone.

Optional Daylight Saving Time reminders notify communities when local viewing times may differ.

---

# 7. Watch History

Every completed viewing becomes part of the permanent Watch History.

History records include:

- Watch date
- Event type
- Winning vote
- Original suggester
- Rewatch number

Rewatchs do not create duplicate Watch Items.

Instead, each viewing becomes another entry within the Watch Item Journey.

---

# 8. Statistics

Watch Party Manager maintains historical statistics for the community.

Examples include:

- Total Watch Items watched
- Most watched genres
- Suggestion success rates
- Watch Item Journey statistics
- Community milestones

Statistics are intended to preserve the story of the community rather than serve as competitive rankings.

---

# 9. Administration Overview

Administrative functions include:

- Configuration
- Nominee selection
- Schedule management
- Manual corrections
- Import and export
- Backup and restore

Detailed administrative behavior is documented in the Administration specification.
