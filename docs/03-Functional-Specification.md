# Watch Party Manager

## Functional Specification

| Property     | Value                            |
| ------------ | -------------------------------- |
| Document     | Functional Specification         |
| File         | `03-Functional-Specification.md` |
| Version      | 1.0 Draft                        |
| Status       | Draft                            |
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
4. Rotation Management
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

Every Watch Item progresses through one or more states.

```text
Suggested
    │
    ▼
Eligible
    │
    ▼
Current Rotation
    │
    ▼
Selected for Vote
    │
    ▼
Scheduled
    │
    ▼
Watched
    │
    ▼
Rewatch Eligible
```

A Watch Item may return to the Current Rotation multiple times throughout its lifetime.

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
- Rotation history
- Number of voting appearances
- Winning vote
- Watch dates
- Number of rewatches

The Journey is never deleted under normal operation.

---

# 3. Watch Item Suggestions

Community members may suggest Watch Items.

Suggestions become eligible for inclusion during the next rotation refresh.

New suggestions are never inserted into an active rotation.

Duplicate suggestions are automatically detected.

If a Watch Item has already been watched, administrators may choose whether it should become immediately eligible for rewatch or remain retired.

---

# 4. Rotation Management

A rotation represents the current pool of eligible Watch Items.

Each rotation is a snapshot.

New suggestions remain outside the active rotation until the next refresh.

Rotation generation uses the configured pull strategy.

Version 1 includes Adaptive Balanced Pull.

Rotation health is monitored to identify situations such as:

- Low remaining Watch Items
- Genre imbalance
- Excessive repetition

When the rotation approaches exhaustion, Watch Party Manager reminds the community to submit additional suggestions.

The final vote before a rotation refresh is clearly identified.

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

- Rotation Winner
- Manual Assignment
- Birthday Pick
- Holiday Pick

Watch Party Manager may automatically create Discord Events when configured to do so.

Manual Event Series support recurring community traditions without affecting the normal rotation.

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
- Rotation statistics
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
- Rotation statistics
- Watch Item Journey statistics
- Community milestones

Statistics are intended to preserve the story of the community rather than serve as competitive rankings.

---

# 9. Administration Overview

Administrative functions include:

- Configuration
- Rotation management
- Schedule management
- Manual corrections
- Import and export
- Backup and restore

Detailed administrative behavior is documented in the Administration specification.
