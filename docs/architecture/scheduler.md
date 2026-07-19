# Scheduler Architecture

## Purpose

WASH uses one shared persistent scheduler for timed work across voting, reminders, backups, birthdays, watch parties, and future maintenance tasks.

The scheduler determines when work is due and dispatches it to the appropriate feature handler. It does not contain feature-specific business logic.

Example:

1. The scheduler detects that a vote-closing job is due.
2. The scheduler passes the job to the voting handler.
3. The voting workflow closes the round, calculates the result, persists changes, and posts the Discord message.

## Polling Interval

```yaml
scheduler_poll_interval_seconds: 60
```

WASH checks for due jobs every 60 seconds.

Routine polling reads local persistent data. Discord is contacted only when a due job requires a Discord action.

## Execution Model

The scheduler uses persistent polling.

Requirements:

- Scheduled jobs survive bot restarts.
- Overdue jobs are evaluated after startup.
- Jobs use stable identifiers.
- Duplicate logical jobs are prevented.
- Feature handlers recheck current conditions before acting.
- Completed and skipped jobs retain enough history for troubleshooting.
- The scheduler remains independent of Discord-specific and feature-specific business logic.

## Scheduled Job Record

Each job includes:

```yaml
job_id: "<unique identifier>"
guild_id: "<discord guild id>"
job_type: "<registered job type>"
logical_key: "<unique active-job key>"
run_at: "<UTC timestamp>"
status: pending
payload: {}
created_at: "<UTC timestamp>"
started_at: null
completed_at: null
attempt_count: 0
last_error: null
result: null
```

Payloads should contain identifiers rather than copied feature state. Handlers must read current state when the job executes.

## Job Statuses

```text
pending
running
completed
failed
cancelled
```

- `pending`: Waiting for its due time or retry time.
- `running`: Claimed for execution.
- `completed`: Executed successfully or intentionally skipped.
- `failed`: Exhausted automatic retries.
- `cancelled`: Intentionally prevented from executing.

## Job Results

Recommended final results:

```text
executed
skipped_expired
skipped_not_applicable
cancelled
```

A skipped job is normally `completed`, not `failed`, because the scheduler operated correctly.

## Scheduler Loop

Every 60 seconds, WASH should:

1. Query due `pending` jobs where `run_at` is now or earlier.
2. Claim each job by marking it `running`.
3. Increment `attempt_count`.
4. Dispatch it to the registered handler for `job_type`.
5. Mark it `completed` when executed or intentionally skipped.
6. Reschedule it when a retryable error occurs.
7. Mark it `failed` after retry attempts are exhausted.
8. Log failures for WASH Crew troubleshooting.

The repository must prevent two scheduler loops from successfully claiming the same job.

## Retry Policy

Default retry schedule:

```text
First retry: 1 minute after failure
Second retry: 5 minutes after failure
Third retry: 15 minutes after failure
After 3 failed attempts: failed
```

Validation failures, missing records, expired reminders, and other non-retryable conditions should be skipped or failed immediately according to handler policy.

## Duplicate Protection

Each logical action receives a stable `logical_key`.

Examples:

```text
close_vote:<vote_id>
vote_reminder:<vote_id>
non_voter_reminder:<vote_id>:<member_id>
watch_reminder:<watch_party_id>
automatic_backup:<guild_id>:<scheduled_occurrence>
birthday_check:<guild_id>:<calendar_date>
```

Before creating a job, WASH checks for an active job with the same logical key.

Active statuses are `pending` and `running`.

## Overdue Job Policies

### Always run when overdue

- Close vote
- Finalize vote results
- Automatic backup
- Birthday eligibility check
- Cleanup and retention work

### Run within a grace period

| Job | Grace policy |
|---|---|
| Vote participation reminder | Up to 2 hours after its scheduled time |
| Watch-party reminder | Until the watch party begins |
| Non-voter reminder | Until voting closes |

After the useful period expires, mark the job `completed` with `skipped_expired`.

## Execution-Time Validation

Every feature handler must recheck current state before acting.

Examples:

- Is the vote still open?
- Was the vote already closed manually?
- Has the member already voted?
- Is the reminder still enabled?
- Does the watch party still exist?
- Has the watch-party start time changed?
- Has another workflow already completed the intended action?

When the action is no longer needed, the handler returns `skipped_not_applicable`.

## Initial Job Types

### Vote lifecycle

```text
close_vote
vote_participation_reminder
non_voter_reminder
```

### Watch-party lifecycle

```text
watch_party_reminder
```

### Maintenance

```text
automatic_backup
backup_retention_cleanup
```

### Member events

```text
birthday_eligibility_check
```

Job types are registered with handlers rather than implemented as conditional logic inside the scheduler.

## Cancellation & Rescheduling

- Pending jobs may be cancelled.
- Running jobs are not interrupted. Their handlers must recheck current state.
- Rescheduling cancels the obsolete pending job and creates or updates the replacement.
- Old reminders must not fire after the owning event changes.
- Cancelled jobs remain available for diagnostics.
- If a replacement time is already past, normal overdue policy applies.

## Implementation Boundaries

The scheduler owns:

- Polling
- Finding due jobs
- Claiming jobs
- Dispatching handlers
- Retry timing
- Job lifecycle status
- Persistent execution history
- Duplicate protection

Feature services own:

- Business validation
- Domain changes
- Discord message content
- Permission decisions
- Recipient selection
- Whether a job is still applicable
- Feature-specific results
