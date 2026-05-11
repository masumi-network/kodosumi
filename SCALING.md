# Kodosumi Scaling Enhancements

> Fork: [Bajuzjefe/kodosumi](https://github.com/Bajuzjefe/kodosumi)
> Upstream: [masumi-network/kodosumi](https://github.com/masumi-network/kodosumi)
> Status: Planning / Implementation

This fork adds **production scaling capabilities** to Kodosumi — PostgreSQL, Redis Streams, Temporal durable execution, and Docker Compose. All additions are **opt-in** and **backwards-compatible**. Zero changes to default behavior.

---

## Why These Changes

Kodosumi v1.1.0 is a well-architected runtime for interactive AI agents. However, scaling beyond a single-node development setup reveals concrete bottlenecks:

| Problem | Impact | Solution (this fork) |
|---------|--------|----------------------|
| **SQLite is single-writer, file-bound** | Cannot share DB across cluster nodes, limits to ~1000 concurrent users | PR 1: PostgreSQL support |
| **Spooler polls Ray actors every 0.25s** | Adds latency under load, can't run multiple spoolers | PR 2: Redis Streams event transport |
| **No crash recovery for agent jobs** | If a paid job crashes mid-execution, escrowed payment is stuck | PR 3: Temporal durable execution |
| **No containerized deployment** | High onboarding friction — requires manual Ray cluster setup | PR 4: Docker Compose |

These issues are acknowledged by the upstream project (see issues [#8](https://github.com/masumi-network/kodosumi/issues/8) and [#11](https://github.com/masumi-network/kodosumi/issues/11)).

---

## What's Added

### PR 1: PostgreSQL Support

**Config:** `KODO_EXECUTION_DATABASE=postgresql+asyncpg://user:pass@host:5432/db`

Adds PostgreSQL as an optional database backend for both the admin panel and execution event storage.

**New files:**
- `kodosumi/service/store.py` — `ExecutionStore` protocol + `SqliteExecutionStore` + `PostgresExecutionStore`
- `kodosumi/dtypes.py` — `ExecutionEvent` SQLAlchemy model
- `alembic.ini` + `migrations/` — Schema versioning
- `kodosumi/cli.py` — `koco db --upgrade` command

**How it works:**
- When `KODO_EXECUTION_DATABASE` is not set (default): current per-user SQLite behavior, unchanged
- When set to a PostgreSQL URL: events are stored in a centralized `execution_events` table with indexed `fid` and `username` columns
- The admin DB (`KODO_ADMIN_DATABASE`) also supports PostgreSQL URLs — SQLAlchemy handles both dialects transparently

**Dependencies:** `asyncpg` + `psycopg[binary]` (optional, via `pip install kodosumi[postgres]`)

### PR 2: Redis Streams Event Transport

**Config:** `KODO_EVENT_TRANSPORT=redis`, `KODO_REDIS_URL=redis://localhost:6379/0`

Replaces the polling-based Ray queue event delivery with push-based Redis Streams using consumer groups.

**New files:**
- `kodosumi/transport.py` — `EventProducer`/`EventConsumer` protocols with Ray and Redis implementations

**How it works:**
- When `KODO_EVENT_TRANSPORT=ray` (default): current Ray queue polling, unchanged
- When set to `redis`: each execution gets a Redis stream (`kodo:events:{fid}`). The Tracer publishes events via `XADD`, the Spooler consumes via `XREADGROUP` with blocking reads
- Consumer groups enable **multiple spooler instances** to cooperatively consume events from the same streams — horizontal spooler scaling
- `XACK` marks events as processed; unacknowledged events are automatically redelivered to other consumers

**Dependencies:** `redis[hiredis]` (optional, via `pip install kodosumi[redis]`)

### PR 3: Temporal Durable Execution

**Config:** `KODO_EXECUTION_MODE=temporal`, `KODO_TEMPORAL_HOST=localhost:7233`

Wraps agent job execution in Temporal workflows for crash recovery, automatic retry, and status queries.

**New files:**
- `kodosumi/workflows.py` — `AgentWorkflow` (Temporal workflow definition)
- `kodosumi/activities.py` — `execute_agent` (Temporal activity wrapping existing Runner)
- `kodosumi/temporal_worker.py` — Worker process

**How it works:**
- When `KODO_EXECUTION_MODE=direct` (default): current direct Ray actor execution, unchanged
- When set to `temporal`: `Launch()` starts a Temporal workflow instead of directly creating a Runner
- The Temporal activity calls `create_runner()` exactly as `Launch()` does today — all event streaming, forms, locks, and tracing work identically
- Temporal adds durability **around** the execution boundary:
  - If the worker crashes, Temporal retries the activity (up to 3 attempts, exponential backoff)
  - Heartbeats every 5s detect worker death within 120s
  - Workflow signals map to pause/resume (Lock mechanism)
  - Workflow queries return job status without polling the DB
- New CLI command: `koco temporal-worker`

**Dependencies:** `temporalio` (optional, via `pip install kodosumi[temporal]`)

### PR 4: Docker Compose

Provides containerized deployment for all configurations.

**New files:**
- `Dockerfile` — Python 3.11-slim with all optional dependencies
- `docker-compose.yml` — Full stack: Panel + Spooler + Ray + PostgreSQL + Redis
- `docker-compose.dev.yml` — Lightweight: Kodosumi + Ray only (SQLite defaults)
- `docker-compose.override.yml.example` — Template for custom config
- `.dockerignore`

**Usage:**
```bash
# Lightweight dev (SQLite + Ray polling)
docker compose -f docker-compose.dev.yml up

# Full stack (PostgreSQL + Redis)
docker compose up

# Full stack + Temporal (durable execution)
docker compose --profile temporal up
```

---

## What's NOT Changed

These core modules remain completely untouched:

| Module | Description |
|--------|-------------|
| `kodosumi/serve.py` | ServeAPI — the user-facing FastAPI wrapper for agent flows |
| `kodosumi/service/auth.py` | Login/logout endpoints |
| `kodosumi/service/jwt.py` | JWT authentication middleware |
| `kodosumi/service/role.py` | User/role management |
| `kodosumi/service/flow.py` | Flow registration |
| `kodosumi/service/proxy.py` | Proxy routing + LockController |
| `kodosumi/service/health.py` | Health check endpoint |
| `kodosumi/runner/formatter.py` | Output formatting (Markdown, ANSI, YAML) |
| `kodosumi/runner/files.py` | Async/sync file system wrapper |
| `kodosumi/response.py` | Response helpers |
| `kodosumi/error.py` | Exception definitions |
| `kodosumi/log.py` | Logging configuration |
| All admin templates & static assets | Admin panel UI |

**The public API (`ServeAPI`, `Launch`, `Tracer`, `Forms`) is unchanged.** Existing agents work without modification.

---

## Backwards Compatibility

Every feature uses a **default-off** pattern:

| Setting | Default | Effect of Default |
|---------|---------|-------------------|
| `KODO_EXECUTION_DATABASE` | `None` | Per-user SQLite files (current behavior) |
| `KODO_EVENT_TRANSPORT` | `"ray"` | Ray queue polling (current behavior) |
| `KODO_EXECUTION_MODE` | `"direct"` | Direct Ray actor execution (current behavior) |

No new required dependencies are added. Optional features require explicit `pip install kodosumi[postgres]`, `kodosumi[redis]`, or `kodosumi[temporal]`.

**Upgrade path:** `pip install --upgrade kodosumi` — everything works exactly as before. New features are activated only by setting the corresponding environment variables.

---

## Architecture

### Before (upstream)

```
Launch() → Runner (Ray actor) → ray.util.queue.Queue → Spooler (polling) → SQLite files
```

### After (this fork, all features enabled)

```
Launch() → Temporal Workflow → Runner (Ray actor) → Redis Stream → Spooler (XREADGROUP) → PostgreSQL
              │                                                          │
              │ (crash recovery,                              (consumer groups,
              │  retry, signals)                               multiple spoolers)
              │
         Temporal Worker
         (heartbeats, retry)
```

### After (this fork, default config)

```
Launch() → Runner (Ray actor) → ray.util.queue.Queue → Spooler (polling) → SQLite files
```

Identical to upstream. Zero overhead from unused features.

---

## Configuration Reference

### PostgreSQL (PR 1)

```bash
# Admin database (replaces SQLite for user/role storage)
KODO_ADMIN_DATABASE=postgresql+asyncpg://user:pass@localhost:5432/kodosumi

# Execution events (replaces per-user SQLite files)
KODO_EXECUTION_DATABASE=postgresql+asyncpg://user:pass@localhost:5432/kodosumi
```

### Redis Streams (PR 2)

```bash
KODO_EVENT_TRANSPORT=redis          # "ray" (default) or "redis"
KODO_REDIS_URL=redis://localhost:6379/0
KODO_REDIS_STREAM_PREFIX=kodo:events:
KODO_REDIS_CONSUMER_GROUP=spooler
KODO_REDIS_BLOCK_MS=1000            # Consumer blocking timeout
KODO_REDIS_MAX_STREAM_LEN=10000     # Per-stream max entries
```

### Temporal (PR 3)

```bash
KODO_EXECUTION_MODE=temporal        # "direct" (default) or "temporal"
KODO_TEMPORAL_HOST=localhost:7233
KODO_TEMPORAL_NAMESPACE=default
KODO_TEMPORAL_TASK_QUEUE=kodosumi-agents
KODO_TEMPORAL_WORKFLOW_ID_PREFIX=kodo-
KODO_TEMPORAL_EXECUTION_TIMEOUT=3600  # Max job duration (seconds)
```

---

## PR Merge Order

```
PR 1 (PostgreSQL)  ──┐
                     ├── PR 4 (Docker Compose)
PR 2 (Redis)  ──────┤
      │              │
      └── PR 3 (Temporal) ──┘
```

PRs 1 and 2 are independent. PR 3 builds on PR 2's transport abstraction. PR 4 composes all services.

---

## Related Upstream Issues

- [#8 — Scale the spooler component](https://github.com/masumi-network/kodosumi/issues/8) (PRs 1, 2)
- [#11 — Provide containers](https://github.com/masumi-network/kodosumi/issues/11) (PR 4)

---

## Documentation

- [Research & Analysis](./docs/RESEARCH.md) — Full platform evaluation, alternative stacks comparison, strategic assessment
- [Implementation Plan](./docs/IMPLEMENTATION_PLAN.md) — Detailed file-by-file implementation specification
