# Kodosumi Timer Service

This document captures the design decisions and implementation plan for the timer service.

## Overview

The timer service (`koco timer`) schedules and launches agentic services based on cron expressions configured in flow meta. It runs as a Ray cluster service, following the pattern established by the spooler.

## Design Decisions

### Schedule Format: Cron Only

- **Decision**: Use cron expressions exclusively (e.g., `0 9 * * MON-FRI`)
- **Rationale**: Cron is well-understood, powerful, and handles complex schedules. Periodic intervals can be expressed as cron.
- **Library**: `croniter` for parsing and calculating next run times

### User Context: System User

- **Decision**: All timer-triggered executions run as special `timer` system user
- **Rationale**: Clear distinction between manual and scheduled executions in logs and monitoring
- **Implementation**: `TIMER_USER = "timer"` constant

### Input Source: Flow Meta YAML

- **Decision**: Schedule configuration and default inputs defined in `meta[].data` YAML per flow
- **Rationale**: Keeps configuration with the flow definition, editable via Admin UI
- **Format**:
  ```yaml
  schedule:
    cron: "0 9 * * MON-FRI"
    inputs:
      param1: value1
    enabled: true
  ```

### Missed Schedules: Run Once

- **Decision**: If schedules were missed (e.g., timer was down), run once immediately, skip duplicates
- **Rationale**: Prevents flood of executions after outage while ensuring work isn't lost
- **Algorithm**: Calculate next_run from current time, not from last missed time

### Concurrency: Skip If Running

- **Decision**: Skip scheduled execution if a timer-triggered instance is still running
- **Rationale**: Prevents resource exhaustion from overlapping long-running jobs
- **Implementation**: Check Runner actor state via `list_actors()`, track by schedule key
- **Manual Override**: Manual executions are independent and always allowed

## Architecture

### Components

```
┌──────────────────────────────────────────────────────────────┐
│                     Ray Cluster                              │
│  ┌─────────────┐  ┌─────────────┐   ┌─────────────────────┐  │
│  │ TimerLock   │  │ Timer       │   │ Runner Actors       │  │
│  │ (Ray Actor) │  │ (Process)   │   │ (per execution)     │  │
│  │ - pid       │◄─┤ - main loop │──►│ - agent function    │  │
│  │ - schedules │  │ - croniter  │   │ - tracer            │  │
│  │ - running   │  │ - trigger   │   └─────────────────────┘  │
│  └─────────────┘  └─────────────┘                            │
└──────────────────────────────────────────────────────────────┘
         ▲                 │
         │                 ▼
    ┌────┴────┐     ┌─────────────┐
    │ CLI     │     │ expose.db   │
    │ koco    │     │ (schedules) │
    │ timer   │     └─────────────┘
    └─────────┘
```

### Main Loop

```
EVERY TIMER_INTERVAL seconds:
  1. Query expose.db for enabled exposes
  2. Parse meta[].data YAML for schedule configs
  3. For each schedule:
     a. Skip if disabled
     b. Skip if timer-triggered Runner still ALIVE
     c. Use croniter to check if due
     d. If overdue: run once, update next_run from now
     e. call create_runner(username="timer", extra={triggered_by: "timer"})
     f. Update TimerLock state
  4. Sleep interval
```

## Files

### New Files

| File | Purpose |
|------|---------|
| `kodosumi/timer.py` | Core timer: TimerLock actor, Timer class, main loop |
| `kodosumi/service/timer/__init__.py` | Package exports |
| `kodosumi/service/timer/models.py` | Pydantic models for schedule config and status |
| `kodosumi/service/timer/control.py` | Litestar API controller |
| `tests/test_timer.py` | Unit and integration tests |

### Modified Files

| File | Change |
|------|--------|
| `kodosumi/cli.py` | Add `koco timer` command |
| `kodosumi/config.py` | Add `TIMER_*` settings |
| `kodosumi/const.py` | Add `TIMER_NAME`, `TIMER_USER` |
| `kodosumi/service/expose/models.py` | Add schedule section to meta template |
| `kodosumi/runner/main.py` | Add timer metadata to executions |
| `kodosumi/log.py` | Add `timer_logger()` |
| `kodosumi/service/app.py` | Register TimerControl |
| `pyproject.toml` | Add `croniter>=2.0.0` dependency |

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `KODO_TIMER_LOG_FILE` | `./data/timer.log` | Log file path |
| `KODO_TIMER_LOG_FILE_LEVEL` | `DEBUG` | File log level |
| `KODO_TIMER_STD_LEVEL` | `INFO` | Console log level |
| `KODO_TIMER_INTERVAL` | `60` | Polling interval (seconds) |
| `KODO_TIMER_USER` | `timer` | System user for executions |

## CLI Usage

```bash
# Start timer (background)
koco timer --start

# Start timer (foreground/blocking)
koco timer --start --block

# Stop timer
koco timer --stop

# Check status
koco timer --status

# Custom interval
koco timer --start --interval 30
```

## Schedule Configuration

In expose `meta[].data` YAML:

```yaml
# ... other flow metadata ...

# Schedule configuration for automated execution
schedule:
  # Cron expression (minute hour day month weekday)
  cron: "0 9 * * MON-FRI"    # 9 AM weekdays
  # cron: "*/15 * * * *"     # Every 15 minutes
  # cron: "0 0 1 * *"        # First of month

  # Default inputs for scheduled execution
  inputs:
    query: "daily report"
    limit: 100

  # Enable/disable this schedule
  enabled: true
```

## Edge Cases

| Case | Handling |
|------|----------|
| Missed schedules | Run once immediately, set next_run from now |
| Already running | Skip, log debug message |
| Ray Serve down | Skip, log error, retry next interval |
| Invalid cron | Validate in Pydantic, skip with warning |
| Expose deleted | Reconcile each loop, remove stale state |
| Timer restart | Reload state from TimerLock actor |

## Execution Identification

Timer-triggered executions are distinguished by:

```python
username = "timer"
extra = {
    "triggered_by": "timer",
    "schedule_key": "expose_name/url"
}
```

This metadata appears in:
- Execution database (monitor table)
- Admin panel execution list
- Runner actor state

## Future Considerations

- **Persistence**: Timer state is currently in-memory (TimerLock actor). Could add SQLite persistence for crash recovery across Ray restarts.
- **Timezone support**: Cron expressions currently UTC. Could add `timezone` field to schedule config.
- **Retry policy**: Currently no automatic retry on execution failure. Could add `retry_count`, `retry_delay` options.
- **Webhooks**: Could add webhook notifications for schedule events.
