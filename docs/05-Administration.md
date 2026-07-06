# Watch Party Manager

## Administration

| Property     | Value                  |
| ------------ | ---------------------- |
| Document     | Administration         |
| File         | `05-Administration.md` |
| Version      | 1.0 Draft              |
| Status       | Draft                  |
| Last Updated | July 2026              |
| Authors      | TehKarmah & ChatGPT    |

---

> [!NOTE]
> This document defines the administrative capabilities of Watch Party Manager. It describes how server administrators configure, maintain, and operate the application after installation.

---

## Table of Contents

1. Overview
2. User Roles
3. Initial Setup
4. Configuration
5. Rotation Administration
6. Event Administration
7. Watch Item Administration
8. Data Management
9. Monitoring and Maintenance

---

# 1. Overview

Watch Party Manager is designed so that routine administration can be performed entirely through Discord.

Server administrators should not need to edit configuration files or access the database during normal operation.

---

# 2. User Roles

Watch Party Manager supports three administrative permission levels.

## Server Administrator

Responsible for:

- Initial setup
- Global configuration
- Backup and restore
- Import and export
- Updating the application

---

## Watch Party Administrator

Responsible for:

- Managing rotations
- Scheduling events
- Managing Watch Items
- Correcting historical records
- Publishing Discord Events

---

## Watch Party Member

Responsible for:

- Suggesting Watch Items
- Voting
- Viewing statistics
- Managing personal preferences

---

# 3. Initial Setup

The setup wizard guides administrators through the initial configuration.

Configuration includes:

- Assistant name
- Announcement channel
- Watch Party Backlot
- Watchlist thread
- Watch history thread
- Voice channel selection or creation
- Watch Party role
- Watch Party Administrator role
- Scheduling timezone
- Default Event Series
- Voting policies
- Birthday policies
- Event creation preferences

The setup wizard may be run again at any time.

---

# 4. Configuration

Most behavior within Watch Party Manager is configurable.

Examples include:

## Voting

- Blind voting
- Number of candidates
- Maximum vote changes
- Automatic closing
- Reminder timing

---

## Rotation

- Adaptive Balanced Pull
- Rotation refresh behavior
- Low rotation reminders

---

## Scheduling

- Event Series
- Voice channels
- Scheduling timezone
- Automatic Discord Events
- Daylight Saving Time reminders

---

## Community Features

- Birthday picks
- Holiday picks
- Monthly special events
- Television nights

---

# 5. Rotation Administration

Watch Party Administrators may:

- Preview the next pull
- Generate a pull
- Refresh the rotation
- Override Adaptive Balanced Pull
- Postpone a refresh
- Review rotation health

Rotation changes are recorded in the audit log.

---

# 6. Event Administration

Administrators may:

- Create Event Series
- Edit Event Series
- Disable Event Series
- Assign Watch Items
- Move scheduled events
- Postpone events
- Skip weeks
- Publish Discord Events

Recurring community traditions are managed through Event Series rather than individual event rules.

---

# 7. Watch Item Administration

Administrators may:

- Edit Watch Item information
- Merge duplicate Watch Items
- Mark Watch Items as watched
- Return Watch Items to rotation
- Add manual watch history
- Correct historical records

Historical information should be preserved whenever practical.

---

# 8. Data Management

Watch Party Manager provides administrative tools for:

- Import
- Export
- Backup
- Restore

Communities retain ownership of their data.

Backups are intended to be portable between installations.

---

# 9. Monitoring and Maintenance

Administrative monitoring includes:

- Health status
- Audit log
- Scheduled jobs
- Backup status
- Database integrity

The health dashboard provides a summary of the current operational state of the application.

Routine maintenance should be automatic whenever practical.

Administrative intervention should be required only for exceptional situations.
