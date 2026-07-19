# Guild Configuration Specification

## Purpose & Scope

Guild Configuration stores settings specific to one Discord server. It is separate from application-wide configuration, future member configuration, runtime configuration, and database-owned configuration.

This specification defines persistence infrastructure only. It does not define `/setup`, `/config`, or Discord UI behavior.

## Guild Metadata

```yaml
schema_version: 1
guild_id: "<discord guild id>"
guild_name: "<guild name>"
setup_completed: false
created_at: "<utc timestamp>"
updated_at: "<utc timestamp>"
configuration_version: 1
```

`guild_id` is the immutable primary key. `configuration_version` starts at 1 and increments after every successful update. `created_at` never changes after creation. `updated_at` is refreshed after every successful update.

## Roles

```yaml
wash_crew_role_id: null
administrator_override: true
watch_party_role:
  role_id: null
  join_mode: self_service
  allow_self_leave: true
```

Supported join modes are `manual`, `self_service`, `approval`, and `discord_managed`.

## Suggestion Databases

```yaml
suggestion_databases:
  movies:
    id: "movies"
    display_name: "Movies"
    active: true
```

Database IDs are permanent and unique within the guild configuration. Deactivation is the normal removal workflow. Deactivated databases preserve suggestions, votes, watch history, archive records, and statistics, and may be reactivated later. Permanent deletion is not part of v1.

This lightweight guild entry does not replace the existing operational `SuggestionDatabase` model. Reconciliation of string configuration IDs with the current numeric operational IDs must be handled explicitly in a later database-configuration milestone.

## Guild-Wide Channels

```yaml
channels:
  announcements_channel_id: null
  log_channel_id: null
```

Both channels are optional. When no announcements channel is configured, WASH may use the current interaction channel where appropriate. When no log channel is configured, noncritical Discord logging is suppressed; critical failures still go to application logs.

## Voting Defaults

```yaml
voting_defaults:
  candidate_count: 3
  duration_days: 7
  visibility: blind
  max_vote_changes: 1
  tie_behavior: all_winners
```

Validation:

- `candidate_count`: 2 through 10
- `duration_days`: 1 through 30
- `visibility`: `blind` or `visible`
- `max_vote_changes`: 0 through 10
- `tie_behavior`: `all_winners` in v1

Runoff and other tie strategies are deferred.

## Notifications

```yaml
notifications:
  vote:
    vote_started: true
    vote_results: true
    vote_ending_reminder: true
    reminder_hours_before_close: 24
  watch:
    enabled: true
    reminder_hours_before_watch: 1
  administrative:
    low_suggestion_pool: true
    low_suggestion_pool_threshold: 10
    backup_completed: true
    backup_failed: true
    restore_completed: true
    restore_failed: true
```

Reminder intervals use positive whole hours. Member-specific reminders remain opt-in, default off, and belong to future Member Configuration.

## Feature Flags

```yaml
feature_flags:
  birthday_picks: false
  self_service_watch_party_role: true
  member_vote_reminders: true
  watch_reminders: true
  low_suggestion_pool_alerts: true
  suggestion_rejection_voting: true
  archived_suggestion_review: true
```

Birthday picks remain disabled by default until the feature is fully designed.

## Backup Configuration

```yaml
backup:
  include_in_automatic_backups: true
  notify_on_backup_success: true
  notify_on_backup_failure: true
  allow_restore: true
```

Application Configuration owns backup schedule, retention, storage location, and archive format.

## Watch History

```yaml
watch_history:
  enabled: true
  allow_retroactive_entries: true
  allow_repeat_watches: true
```

Repeat watches create distinct history entries. Disabling watch history preserves existing records.

## Migration Strategy

```yaml
migration:
  current_schema_version: 1
  automatic_migrations: true
  backup_before_migration: true
  reject_future_schema_versions: true
```

Rules:

- Missing `schema_version` is treated as version 1.
- Migrations are deterministic and sequential.
- A migration must advance exactly one schema version.
- The persistence file is backed up before an older schema is migrated.
- Schema versions newer than the running application supports are rejected.
- Migration and validation failures must not partially save data.
- Migration must not depend on a Discord connection.

## Persistence & Validation Rules

- Configuration fails closed when required settings are absent or invalid.
- `guild_id` is immutable once persisted.
- Failed validation does not alter the saved configuration.
- Unknown fields at the guild level and inside nested sections are preserved through load and save cycles.
- Optional Discord IDs are stored as null when unconfigured and must otherwise be positive integers.
- All timestamps are timezone-aware UTC-compatible values.
- Writes are atomic at the file level.
