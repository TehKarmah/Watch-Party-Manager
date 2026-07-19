
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

| Field | Description |
| --------------------- | ------------------------------------------------------ |
| schema_version | Configuration schema version used for migration. |
| guild_id | Discord Guild ID. Primary key. |
| guild_name | Cached guild name for diagnostics. |
| setup_completed | Indicates whether `/setup` has completed successfully. |
| created_at | UTC timestamp when configuration was created. |
| updated_at | UTC timestamp of last modification. |
| configuration_version | Incremented whenever configuration changes. |

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

Membership is managed manually by Server Owner, Discord Administrators, or WASH Crew.

#### self_service

Members manage their own membership using `/join` and `/leave`.

#### approval

Members request membership. WASH Crew reviews and approves or denies requests.

#### discord_managed

Discord manages role assignment. WASH validates membership but does not assign the role.

## Self Leave

```yaml
allow_self_leave: true
```

Default: **true**

---

# Permission Model

Permission evaluation order:

1. Server Owner
2. Discord Administrator (when administrator override is enabled)
3. WASH Crew
4. Watch Party
5. Everyone

| Command | Minimum Permission |
|---|---|
| `/about` | Everyone |
| `/help` | Everyone |
| `/status` | Everyone |
| `/list` | Everyone |
| `/join` | Everyone |
| `/add` | Watch Party |
| `/vote` | Watch Party |
| `/leave` | Watch Party |
| `/start_vote` | WASH Crew |
| `/config` | WASH Crew |
| `/setup` | WASH Crew |

---

# Suggestion Databases

Suggestion Databases organize independent collections of watch suggestions.

Each database maintains its own settings while Guild Configuration stores the list of available databases.

```yaml
suggestion_databases:
  movies:
    id: "movies"
    display_name: "Movies"
    active: true
```

Each database has a permanent unique identifier. Display names may change. Inactive databases preserve history but cannot accept suggestions or start voting.

Future database-owned configuration includes:

- Suggestion Channel
- Voting Channel
- Watch History
- Archive Settings
- Voting Defaults
- Recommendation Engine Settings
- Statistics
- Moderators
- Genre Preferences
- Scheduling Defaults

---

# Channels

Guild Configuration stores guild-wide Discord channels that are not owned by an individual suggestion database.

```yaml
channels:
  announcements_channel_id: "<discord channel id>"
  log_channel_id: "<discord channel id>"
```

Channels dedicated to a specific suggestion database are configured with that database, not here.

Unconfigured optional channels are stored as null.

---

# Future Sections

- Voting Defaults
- Notifications
- Feature Flags
- Watch History
- Backup
- Validation Rules
- Default Values
- Migration Strategy
