# Watch Party Manager

## Developer Guide

| Property     | Value                   |
| ------------ | ----------------------- |
| Document     | Developer Guide         |
| File         | `06-Developer-Guide.md` |
| Version      | 1.0 Draft               |
| Status       | Draft                   |
| Last Updated | July 2026               |
| Authors      | TehKarmah & ChatGPT     |

---

> [!NOTE]
> This document provides technical guidance for developers implementing or contributing to Watch Party Manager. It complements the Product Vision, Architecture, Functional Specification, Data Model, and Administration documents.

---

## Table of Contents

1. Purpose
2. Development Philosophy
3. Recommended Technology Stack
4. Project Structure
5. Coding Standards
6. Database Guidelines
7. Logging
8. Testing
9. Versioning
10. Future Development

---

# 1. Purpose

The Developer Guide establishes technical standards for implementing Watch Party Manager.

It does not define application behavior. Functional requirements are documented in the Functional Specification.

---

# 2. Development Philosophy

Development should prioritize:

- Readability
- Maintainability
- Reliability
- Modularity
- Simplicity

Code should be understandable before it is clever.

Whenever practical, business logic should remain separate from Discord-specific functionality.

---

# 3. Recommended Technology Stack

Version 1 is designed around the following technologies.

| Component       | Recommendation |
| --------------- | -------------- |
| Language        | Python         |
| Discord Library | discord.py     |
| Database        | SQLite         |
| Configuration   | JSON           |
| Source Control  | Git            |
| Repository      | GitHub         |
| Documentation   | Markdown       |
| Hosting         | Self-hosted    |

Alternative implementations are acceptable provided they satisfy the Functional Specification.

---

# 4. Project Structure

The recommended project layout is:

```text
Watch-Party-Manager/
│
├── docs/
├── src/
├── tests/
├── data/
├── README.md
├── LICENSE
├── CHANGELOG.md
└── .gitignore
```

Within `src/`, modules should remain organized by responsibility rather than by command.

Example modules include:

- Discord Interface
- Scheduler
- Rotation Engine
- Voting Engine
- Statistics
- Database
- Metadata Providers

---

# 5. Coding Standards

Developers should follow these principles.

## Naming

Names should clearly describe their purpose.

Avoid abbreviations unless they are widely recognized.

---

## Comments

Comments should explain **why**, not **what**.

Good code should already explain what it is doing.

---

## Error Handling

Unexpected situations should produce meaningful log entries.

Errors presented to users should remain friendly and avoid exposing implementation details.

---

## Configuration

Values that communities may reasonably wish to change should be configurable rather than hardcoded.

---

# 6. Database Guidelines

The database should prioritize:

- Data integrity
- Historical accuracy
- Easy backup
- Easy migration

Destructive updates should be avoided whenever practical.

Historical records should remain available for reporting.

---

# 7. Logging

Logging should support both troubleshooting and auditing.

Logs should include:

- Startup
- Shutdown
- Errors
- Administrative actions
- Scheduling events
- Vote processing
- Backup operations

Logging verbosity should be configurable.

---

# 8. Testing

Automated testing should verify:

- Rotation logic
- Voting logic
- Scheduling
- Statistics
- Import and export
- Backup and restore

Regression tests should accompany bug fixes whenever practical.

---

# 9. Versioning

Watch Party Manager follows semantic versioning.

Examples:

- 1.0.0
- 1.1.0
- 1.1.1
- 2.0.0

Major versions may introduce breaking changes.

Minor versions introduce new functionality without breaking compatibility.

Patch versions address defects.

---

# 10. Future Development

Future contributions should align with the Product Vision and Design Principles.

Before introducing significant new functionality, contributors should consider:

- Does this support the mission of Watch Party Manager?
- Is the feature broadly useful?
- Can it be configured?
- Does it preserve existing community data?
- Does it maintain accessibility?
- Does it integrate cleanly with the existing architecture?

Features that significantly expand the scope of the application should be documented in `Future-Ideas.md` before implementation.
