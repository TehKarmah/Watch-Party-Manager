# Watch Party Manager

## Data Model

| Property     | Value               |
| ------------ | ------------------- |
| Document     | Data Model          |
| File         | `04-Data-Model.md`  |
| Version      | 1.0 Draft           |
| Status       | Draft               |
| Last Updated | July 2026           |
| Authors      | TehKarmah & ChatGPT |

---

> [!NOTE]
> This document defines the logical data model used by Watch Party Manager. It describes the major entities, their relationships, and the information they store. It intentionally avoids implementation-specific database details.

---

## Table of Contents

1. Overview
2. Design Goals
3. Core Entities
4. Entity Relationships
5. Data Ownership
6. Data Retention

---

# 1. Overview

The Watch Party Manager data model is designed around a small number of core entities.

Each entity represents a real-world concept used by the application.

The model prioritizes long-term historical accuracy, extensibility, and portability over minimizing storage requirements.

---

# 2. Design Goals

The data model should:

- Preserve complete historical records.
- Support multiple Discord servers independently.
- Allow future media types without redesign.
- Support recurring event series.
- Record community history over many years.
- Support backup and migration.
- Minimize duplicate data.

---

# 3. Core Entities

The following entities form the foundation of the application.

## Server

Represents a single Discord server using Watch Party Manager.

Stores:

- Configuration
- Roles
- Channels
- Scheduling policies
- Feature settings

---

## Member

Represents a Discord user within a server.

Stores:

- Discord User ID
- Display name
- Optional birthday
- Optional timezone
- Statistics
- Participation history

---

## Watch Item

Represents a movie or television series managed by the community.

Stores:

- Title
- Media type
- Runtime
- Genres
- Metadata provider identifiers
- Current status

A Watch Item exists only once regardless of how many times it is watched.

---

## Suggestion

Represents the original suggestion of a Watch Item.

Stores:

- Suggesting member
- Suggestion date
- Optional notes

---

## Rotation

Represents a snapshot of eligible Watch Items.

Stores:

- Rotation number
- Creation date
- Rotation status
- Eligible Watch Items

---

## Vote

Represents a single voting event.

Stores:

- Candidate Watch Items
- Individual votes
- Winning Watch Item(s)
- Voting configuration
- Close date

---

## Event Series

Represents a recurring scheduling rule.

Examples include:

- Weekly Watch Party
- Television Night
- Monthly Special Event

Stores:

- Schedule
- Source type
- Voice channel
- Default settings

---

## Scheduled Event

Represents one occurrence of an Event Series.

Stores:

- Scheduled Watch Item
- Date
- Time
- Discord Event ID
- Status

---

## Watch Record

Represents one completed viewing.

Stores:

- Watch date
- Event type
- Rewatch number
- Winning vote
- Rotation information

A Watch Item may have multiple Watch Records.

---

## Audit Log

Records administrative changes.

Examples include:

- Schedule changes
- Configuration updates
- Manual corrections
- Imports
- Restores

---

# 4. Entity Relationships

The following relationships exist between major entities.

- One Server contains many Members.
- One Server contains many Watch Items.
- One Watch Item may have many Watch Records.
- One Rotation contains many Watch Items.
- One Vote contains multiple candidate Watch Items.
- One Event Series creates many Scheduled Events.
- One Scheduled Event may produce one Watch Record.
- One Member may submit many Suggestions.

---

# 5. Data Ownership

Every primary entity belongs to exactly one Discord server.

This ensures complete separation between communities while allowing a single Watch Party Manager instance to support multiple servers.

No data is shared between servers unless explicitly exported and imported.

---

# 6. Data Retention

Historical records are intended to be permanent.

Normal operation should never delete:

- Watch Records
- Suggestions
- Vote history
- Rotation history
- Audit history

Instead, records transition between active and historical states while remaining available for reporting and statistics.

Only explicit administrative actions may permanently remove historical data.
