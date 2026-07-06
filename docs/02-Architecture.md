# Watch Party Manager

## Architecture

| Property     | Value                |
| ------------ | -------------------- |
| Document     | Architecture         |
| File         | `02-Architecture.md` |
| Version      | 1.0 Draft            |
| Status       | Draft                |
| Last Updated | July 2026            |
| Authors      | TehKarmah & ChatGPT  |

---

> [!NOTE]
> This document defines the overall architecture of Watch Party Manager. It describes the major system components, their responsibilities, and how they interact. Detailed implementation and database design are documented separately.

---

## Table of Contents

1. Architectural Overview
2. Architectural Principles
3. System Components
4. System Data Flow
5. External Services
6. Scalability
7. Future Expansion
8. Architectural Constraints

---

# 1. Architectural Overview

Watch Party Manager (WPM) is a self-hosted Discord application built around a modular architecture.

Each major subsystem has a clearly defined responsibility and communicates through well-defined interfaces. This separation allows new functionality to be added with minimal impact on existing components while keeping the codebase maintainable.

The architecture is designed to support both small private communities and larger public Discord servers without requiring significant redesign.

Business logic is intentionally separated from Discord-specific functionality, allowing the core application to evolve independently of the user interface.

---

# 2. Architectural Principles

The architecture follows several guiding principles.

## Separation of Responsibilities

Each subsystem performs one primary function.

Examples include:

- Discord communication
- Scheduling
- Rotation management
- Voting
- Statistics
- Database access
- Metadata retrieval

Business logic should never be tightly coupled to Discord-specific functionality.

---

## Discord Is the Primary Interface

Discord is the primary user interface for Version 1, not the application itself.

The Watch Party Engine performs business logic independently of Discord whenever practical.

This separation allows future interfaces, such as web dashboards or additional chat platforms, without requiring major changes to the application core.

---

## Configuration Over Customization

Behavior should be modified through configuration rather than source code changes whenever practical.

Examples include:

- Voting policies
- Recurring Event Series
- Reminder schedules
- Rotation behavior
- Assistant name
- Channel assignments
- Permission roles

---

## Modular Growth

New features should be implemented as independent modules whenever practical.

Future additions should require minimal changes to existing systems.

---

# 3. System Components

Watch Party Manager consists of the following major components.

## Discord Interface

Responsible for:

- Slash commands
- Buttons
- Message formatting
- Discord Event management
- Role detection
- User interaction

The Discord Interface translates user actions into requests for the Watch Party Engine.

---

## Watch Party Engine

Responsible for:

- Watch Item lifecycle
- Rotation management
- Voting logic
- Scheduling decisions
- Statistics generation
- Rule enforcement

The Watch Party Engine is the core of the application and coordinates communication between all other subsystems.

---

## Database Layer

Responsible for:

- Persistent storage
- Configuration
- Watch history
- Vote history
- Statistics
- Audit logs

Version 1 uses SQLite as the primary database engine.

---

## Scheduler

Responsible for:

- Vote rollover
- Event scheduling
- Reminder timing
- Recurring Event Series
- Birthday detection
- Daylight Saving Time monitoring

The Scheduler performs time-based operations independently of user interaction.

---

## Metadata Services

Responsible for retrieving Watch Item metadata from supported providers.

Version 1 supports:

- IMDb
- TMDb

Additional providers may be added in future versions without redesigning the core application.

---

# 4. System Data Flow

Most user interactions follow a common workflow.

```text
Discord User
      │
      ▼
Discord Interface
      │
      ▼
Watch Party Engine
      │
      ├──────────────┐
      ▼              ▼
Database      Metadata Services
      ▲
      │
 Scheduler
```
