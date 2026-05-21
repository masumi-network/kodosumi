# Kodosumi User Timer Service (Alternative Design)

This document describes a user-centric timer where each user manages their own scheduled executions.

## Overview

Unlike the system-level timer (TIMER.md), this approach gives users control over scheduling their own agent executions through the Admin Panel UI.

## Key Differences from System Timer

| Aspect | System Timer (TIMER.md) | User Timer (this doc) |
|--------|------------------------|----------------------|
| Ownership | System-wide, DevOps configured | Per-user, self-service |
| Configuration | In expose meta YAML | Via Admin Panel UI |
| User context | Special `timer` user | Actual user who scheduled |
| Visibility | All users see same schedules | Users see only their own |
| Storage | expose.db / TimerLock | New `schedule.db` per-user or central |

## User Stories

1. **Run Later (One-time)**: "I want to run this agent tomorrow at 9 AM with these inputs"
2. **Run Regularly (Cron)**: "I want this agent to run every Monday at 8 AM"
3. **Manage Schedules**: "Show me my pending/active schedules, let me edit or cancel them"

## Data Model

### Schedule Table (in user's execution directory or central db)

```sql
CREATE TABLE schedule (
    id TEXT PRIMARY KEY,           -- unique schedule ID
    username TEXT NOT NULL,        -- owner
    expose_name TEXT NOT NULL,     -- target expose
    flow_url TEXT NOT NULL,        -- target flow endpoint
    schedule_type TEXT NOT NULL,   -- 'once' | 'cron'
    cron_expression TEXT,          -- for type='cron'
    run_at REAL,                   -- for type='once' (timestamp)
    inputs TEXT,                   -- JSON: input data
    enabled INTEGER DEFAULT 1,
    last_run REAL,
    next_run REAL,
    last_fid TEXT,                 -- last execution ID
    created REAL NOT NULL,
    updated REAL NOT NULL
);

CREATE INDEX idx_schedule_user ON schedule(username);
CREATE INDEX idx_schedule_next ON schedule(next_run) WHERE enabled = 1;
```

### Pydantic Models

```python
class ScheduleCreate(BaseModel):
    expose_name: str
    flow_url: str
    schedule_type: Literal["once", "cron"]
    cron_expression: Optional[str] = None  # required if type='cron'
    run_at: Optional[datetime] = None      # required if type='once'
    inputs: dict = {}

class ScheduleResponse(BaseModel):
    id: str
    username: str
    expose_name: str
    flow_url: str
    schedule_type: str
    cron_expression: Optional[str]
    run_at: Optional[datetime]
    inputs: dict
    enabled: bool
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    last_fid: Optional[str]
    created: datetime
    updated: datetime
```

## API Endpoints

```
POST   /schedule/              Create new schedule
GET    /schedule/              List user's schedules
GET    /schedule/{id}          Get schedule details
PUT    /schedule/{id}          Update schedule
DELETE /schedule/{id}          Delete schedule
POST   /schedule/{id}/trigger  Manually trigger now
POST   /schedule/{id}/pause    Pause schedule
POST   /schedule/{id}/resume   Resume schedule
```

## UI Components

### Schedule Creation Form

```
┌─────────────────────────────────────────────────────────┐
│ Schedule Agent Execution                                 │
├─────────────────────────────────────────────────────────┤
│ Agent: [Dropdown: select from available flows]          │
│                                                         │
│ Schedule Type:                                          │
│   ○ Run Once    ○ Run Regularly                         │
│                                                         │
│ [If Once:]                                              │
│   Date: [____/__/____]  Time: [__:__]                   │
│                                                         │
│ [If Regularly:]                                         │
│   Frequency: [Dropdown: Hourly/Daily/Weekly/Monthly]    │
│   - or -                                                │
│   Cron: [_____________] (advanced)                      │
│                                                         │
│ Inputs:                                                 │
│   [JSON editor or form fields from agent schema]        │
│                                                         │
│ [Schedule]  [Cancel]                                    │
└─────────────────────────────────────────────────────────┘
```

### Schedule List View

```
┌─────────────────────────────────────────────────────────┐
│ My Scheduled Executions                    [+ New]      │
├─────────────────────────────────────────────────────────┤
│ ✓ Daily Report          cron: 0 9 * * *    Next: 9:00  │
│   └─ Last run: 2024-01-15 09:00 (success)              │
│                                                         │
│ ✓ Weekly Cleanup        cron: 0 0 * * MON  Next: Mon   │
│   └─ Last run: 2024-01-08 00:00 (success)              │
│                                                         │
│ ⏸ Data Export           once: 2024-01-20 15:00         │
│   └─ Paused                                            │
└─────────────────────────────────────────────────────────┘
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Admin Panel                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │
│  │ Schedule UI │  │ /schedule/  │  │ schedule.db     │  │
│  │ (Jinja2)    │──│ API         │──│ (per-user or    │  │
│  │             │  │             │  │  central)       │  │
│  └─────────────┘  └─────────────┘  └─────────────────┘  │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                     Timer Service                        │
│  ┌─────────────┐                    ┌─────────────────┐  │
│  │ Timer       │  queries           │ Runner Actors   │  │
│  │ (Ray actor) │──schedule.db──────►│ (executions)    │  │
│  │             │  triggers          │                 │  │
│  └─────────────┘                    └─────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Implementation Changes

### New Files

| File | Purpose |
|------|---------|
| `kodosumi/service/schedule/models.py` | Pydantic models |
| `kodosumi/service/schedule/db.py` | Schedule database operations |
| `kodosumi/service/schedule/control.py` | API controller |
| `kodosumi/service/templates/schedule/` | UI templates |
| `kodosumi/timer.py` | Timer service (shared with system timer) |

### Modified Files

| File | Change |
|------|--------|
| `kodosumi/service/app.py` | Register ScheduleControl |
| `kodosumi/service/templates/layout.html` | Add "Schedules" nav item |

## Comparison: System vs User Timer

**System Timer (TIMER.md):**
- DevOps defines schedules in expose meta
- All instances of a flow share same schedule
- Simpler, no UI needed
- Good for: operational tasks, system maintenance

**User Timer (this doc):**
- Users create their own schedules
- Personal schedules with custom inputs
- Requires UI and per-user storage
- Good for: business automation, recurring reports

## Recommendation

**Implement both**, with priority:
1. **Phase 1**: System timer (TIMER.md) - simpler, enables automation
2. **Phase 2**: User timer (this doc) - adds self-service capability

---

# Alternative Implementation Approaches

## 1. External Scheduler Integration

**Approach**: Integrate with existing schedulers (Airflow, Celery Beat, Temporal)

| Pros | Cons |
|------|------|
| Battle-tested, feature-rich | External dependency |
| Native monitoring/alerting | Complex setup |
| Workflow orchestration | Over-engineered for simple cases |

**Business Priority**: Medium - only if customers already use these tools

## 2. Webhook-Based Scheduling

**Approach**: Expose webhook endpoints, users configure external cron (GitHub Actions, cron.org, etc.)

| Pros | Cons |
|------|------|
| Zero kodosumi changes | Users manage scheduling externally |
| Works today | No visibility in Admin Panel |
| Scalable | Auth complexity |

**Business Priority**: Low - shifts complexity to users

## 3. Database Polling (Current Plan)

**Approach**: Timer polls schedule DB, triggers on due schedules

| Pros | Cons |
|------|------|
| Self-contained | Polling overhead |
| Simple architecture | Latency (up to interval) |
| Works with Ray | State management |

**Business Priority**: High - balances simplicity with features

## 4. Event-Driven with Redis/Message Queue

**Approach**: Use Redis sorted sets or RabbitMQ delayed messages

| Pros | Cons |
|------|------|
| Precise timing | Additional infrastructure |
| No polling | Operational complexity |
| Scalable | Overkill for most cases |

**Business Priority**: Low - unless scale demands it

---

# Business Priority Assessment

| Feature | Priority | Rationale |
|---------|----------|-----------|
| **Cron scheduling (system)** | HIGH | Enables automation, DevOps self-service |
| **Run later (user, once)** | HIGH | Common request: "run this tonight" |
| **Recurring schedules (user)** | MEDIUM | Business reports, regular tasks |
| **Schedule management UI** | MEDIUM | Needed for user self-service |
| **Timezone support** | MEDIUM | International users need this |
| **Retry on failure** | LOW | Nice-to-have, manual retry works |
| **Webhook notifications** | LOW | Can be added later |
| **External scheduler integration** | LOW | Only for enterprise customers |

**Recommended Priority Order:**
1. System timer with cron (TIMER.md) - enables core automation
2. User "run later" (one-time) - quick win, high value
3. User recurring schedules - natural extension
4. UI for schedule management - completes self-service story
