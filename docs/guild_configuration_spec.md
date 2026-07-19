# Guild Configuration Specification

## Purpose

Guild Configuration stores all settings that are specific to a Discord server.

It is separate from:

- Application Configuration (installation-wide settings)
- Member Configuration (future)
- Runtime Configuration (constructed at startup)

---

# Guild Metadata

```yaml
schema_version: 1
guild_id: "<discord guild id>"
guild_name: "<guild name>"
setup_completed: false
created_at: "<utc timestamp>"
updated_at: "<utc timestamp>"
configuration_version: 1
```

## Field Descriptions

| Field                 | Description                                            |
| --------------------- | ------------------------------------------------------ |
| schema_version        | Configuration schema version used for migration.       |
| guild_id              | Discord Guild ID. Primary key.                         |
| guild_name            | Cached guild name for diagnostics.                     |
| setup_completed       | Indicates whether `/setup` has completed successfully. |
| created_at            | UTC timestamp when configuration was created.          |
| updated_at            | UTC timestamp of last modification.                    |
| configuration_version | Incremented whenever configuration changes.            |

---

# Roles

Guild configuration stores only the Discord role identifiers required by WASH.

Permission behavior is defined separately by the Permission Model.

## WASH Crew

```yaml
wash_crew_role_id: "<discord role id>"
administrator_override: true
```

### administrator_override

When enabled, Discord Administrators receive WASH administrative permissions even if they are not members of the WASH Crew role.

Default:

```yaml
administrator_override: true
```

---

## Watch Party

```yaml
watch_party_role:
  role_id: "<discord role id>"
  join_mode: self_service
  allow_self_leave: true
```

### Join Modes

#### manual

Membership is managed manually by:

- Server Owner
- Discord Administrators
- WASH Crew

#### self_service

Members manage their own membership using:

- `/join`
- `/leave`

#### approval

Members request membership.

WASH Crew reviews and approves or denies requests.

Members may still leave if self-leave is enabled.

#### discord_managed

Discord manages role assignment using native features such as:

- Server Onboarding
- Role Links
- Other Discord role management

WASH validates membership but does not assign the role.

---

## Self Leave

```yaml
allow_self_leave: true
```

Default: **true**

Members should always be able to voluntarily leave the Watch Party without requiring approval.

---

# Permission Model

Permission evaluation order:

1. Server Owner
2. Discord Administrator (when administrator override is enabled)
3. WASH Crew
4. Watch Party
5. Everyone

Each command specifies the minimum permission tier required.

Example:

| Command       | Minimum Permission |
| ------------- | ------------------ |
| `/about`      | Everyone           |
| `/help`       | Everyone           |
| `/status`     | Everyone           |
| `/list`       | Everyone           |
| `/join`       | Everyone           |
| `/add`        | Watch Party        |
| `/vote`       | Watch Party        |
| `/leave`      | Watch Party        |
| `/start_vote` | WASH Crew          |
| `/config`     | WASH Crew          |
| `/setup`      | WASH Crew          |

---

# Future Sections

The following sections will be added as the design progresses.

- Channels
- Suggestion Databases
- Voting Defaults
- Notifications
- Feature Flags
- Watch History
- Backup
- Validation Rules
- Default Values
- Migration Strategy
