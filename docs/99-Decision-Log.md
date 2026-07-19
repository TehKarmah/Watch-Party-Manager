# Watch Party Manager

## Decision Log

| Property     | Value                |
| ------------ | -------------------- |
| Document     | Decision Log         |
| File         | `99-Decision-Log.md` |
| Version      | 1.0 Draft            |
| Status       | Living Document      |
| Last Updated | July 2026            |
| Authors      | TehKarmah & ChatGPT  |

---

> [!NOTE]
> This document records significant design decisions made during the development of Watch Party Manager. It explains **why** a decision was made, not just **what** was decided.

---

## Table of Contents

1. Purpose
2. Decision Format
3. Decisions

---

# 1. Purpose

The Decision Log serves as the historical record of important architectural and design decisions.

Unlike the specification, which describes how the software behaves, this document explains the reasoning behind major choices.

Future contributors should consult this document before proposing changes that alter established behavior.

---

# 2. Decision Format

Each decision records:

- The decision.
- The rationale.
- The expected long-term benefit.

---

# 3. Decisions

---

## Decision 001

### Title

Use **Watch Item** instead of **Movie**.

### Decision

The system models Watch Items rather than Movies.

A Watch Item may represent:

- Movie
- Television series
- Television episode
- Documentary
- Future supported media types

### Rationale

This avoids redesigning the data model when additional media types are supported.

### Expected Benefit

Future expansion requires minimal database changes.

---

## Decision 002

### Title

Treat Discord as the user interface.

### Decision

Business logic belongs in the Watch Party Engine rather than Discord commands.

### Rationale

Separating business logic from Discord-specific functionality improves maintainability and allows future interfaces.

### Expected Benefit

Future support for web interfaces or other chat platforms without major redesign.

---

## Decision 003

### Title

Communities configure policies.

### Decision

Behavior should be configurable whenever practical instead of hardcoded.

Examples include:

- Voting rules
- Rotation behavior
- Reminder schedules
- Recurring Event Series
- Assistant name
- Permission roles

### Rationale

Communities have different traditions and workflows.

### Expected Benefit

The software adapts to communities rather than communities adapting to the software.

---

## Decision 004

### Title

Use Recurring Event Series.

### Decision

Recurring events are represented by configurable Event Series rather than individual hardcoded features.

### Rationale

The same scheduling engine can support weekly watch parties, television nights, birthday picks, holiday events, and community traditions.

### Expected Benefit

Simpler scheduling engine and greater flexibility.

---

## Decision 005

### Title

Rotation snapshots are fixed.

### Decision

New suggestions are not added to an active rotation.

They become eligible when the next rotation is generated.

### Rationale

Maintains fairness by ensuring every Watch Item in a rotation has the same opportunity to appear.

### Expected Benefit

Predictable rotations and easier administration.

---

## Decision 006

### Title

Accessibility is a core design principle.

### Decision

Accessibility considerations are included during feature design.

Examples include:

- Clear terminology
- Intuitive emojis
- Progressive disclosure
- No reliance on color alone
- Screen reader friendly messaging where possible

### Rationale

Good accessibility improves usability for everyone.

### Expected Benefit

A more inclusive and intuitive user experience.

---

## Decision 007

### Title

Communities own their data.

### Decision

Watch Party Manager provides import, export, backup, and restore functionality.

### Rationale

Communities should never become dependent upon a hosting provider or implementation.

### Expected Benefit

Long-term data portability and user trust.


## ADR-00X: Configuration Separation

Status: Accepted (Planning)

WASH will separate configuration into:
- Application Configuration
- Guild Configuration
- Member Configuration (future)
- Runtime Configuration

Only secrets such as `DISCORD_TOKEN` and `OMDB_API_KEY` remain outside persisted configuration.