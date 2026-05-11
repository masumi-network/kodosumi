# Kodosumi Scaling Contributions — Implementation Plan

## Context

Kodosumi (v1.1.0, Apache 2.0) is the Ray-based distributed runtime in the Masumi Network ecosystem. It has concrete scaling bottlenecks: SQLite as primary DB, polling-based event delivery, no crash recovery for paid jobs, and no Docker support. Open issues #8 (scale spooler) and #11 (provide containers) confirm the maintainer wants these addressed.

This plan delivers **4 independently mergeable PRs** that add capabilities on top of the existing codebase. All features are opt-in via config — existing behavior is never broken. No new required dependencies.

## Setup

1. Fork `masumi-network/kodosumi` to `jakubstefanik/kodosumi`
2. Clone to `/Users/dominika/Projects/masumi-experimentation/kodosumi`
3. Create feature branches: `feat/postgres-support`, `feat/redis-transport`, `feat/temporal-durability`, `feat/docker-compose`

## PR 1: PostgreSQL Support (alongside SQLite)

**Branch:** `feat/postgres-support` — Addresses issue #8
**Merge order:** First (independent)

### Changes

| File | Action | What |
|------|--------|------|
| `pyproject.toml` | Edit | Add `postgres = ["asyncpg>=0.29.0", "psycopg[binary]>=3.1.0"]` to optional-dependencies |
| `kodosumi/config.py` | Edit | Add `EXECUTION_DATABASE: Optional[str] = None` setting |
| `kodosumi/dtypes.py` | Edit | Add `ExecutionEvent` SQLAlchemy model (fid, username, timestamp, kind, message — indexed) |
| `kodosumi/service/store.py` | Edit | Add `ExecutionStore` Protocol + `SqliteExecutionStore` (wraps current) + `PostgresExecutionStore` |
| `kodosumi/spooler.py` | Edit | Extract `SpoolerWriter` Protocol + `SqliteSpoolerWriter` (wraps current `setup_database`/`save`) + `PostgresSpoolerWriter` (psycopg sync). `Spooler.__init__` accepts writer param |
| `kodosumi/service/app.py` | Edit | If `EXECUTION_DATABASE` set, create second async engine + `PostgresExecutionStore` in app state |
| `kodosumi/cli.py` | Edit | Add `koco db --upgrade` command (Alembic runner) |
| `alembic.ini` | Create | Standard Alembic config pointing to `kodosumi.config` for URL |
| `migrations/env.py` | Create | Reads `KODO_ADMIN_DATABASE` from Settings |
| `migrations/versions/001_initial.py` | Create | Role table + ExecutionEvent table |
| `tests/test_postgres.py` | Create | Unit tests with `@pytest.mark.skipif(not HAS_ASYNCPG)` guard |

### Key Design
- `SpoolerWriter` Protocol with `setup()`, `save()`, `close()` — sync interface (spooler loop is sync)
- PostgreSQL writer uses `psycopg` (sync driver), not `asyncpg`, matching existing spooler's sync pattern
- SQLite remains default; PostgreSQL activated by setting `KODO_EXECUTION_DATABASE`

---

## PR 2: Redis Streams for Event Delivery

**Branch:** `feat/redis-transport` — Addresses issue #8
**Merge order:** Second (independent from PR 1)

### Changes

| File | Action | What |
|------|--------|------|
| `pyproject.toml` | Edit | Add `redis = ["redis[hiredis]>=5.0.0"]` to optional-dependencies |
| `kodosumi/config.py` | Edit | Add `EVENT_TRANSPORT: str = "ray"`, `REDIS_URL`, `REDIS_STREAM_PREFIX`, `REDIS_CONSUMER_GROUP`, `REDIS_BLOCK_MS`, `REDIS_MAX_STREAM_LEN` |
| `kodosumi/transport.py` | Create | Core module: `EventProducer`/`EventConsumer` Protocols, `RayQueueProducer`, `RayQueueConsumer`, `RedisStreamProducer`, `RedisStreamConsumer`, factory functions |
| `kodosumi/runner/tracer.py` | Edit | Replace `self.queue` with `self.producer: EventProducer`. `_put_async` calls `producer.put_async()`, `_put` calls `producer.put_sync()` |
| `kodosumi/runner/main.py` | Edit | `Runner.__init__` creates producer via factory. `create_runner()` passes transport config from settings |
| `kodosumi/spooler.py` | Edit | Add Redis consumer path in `retrieve()`: if `EVENT_TRANSPORT == "redis"`, use `RedisStreamConsumer.read_batch()` + `ack()` instead of Ray queue polling |
| `tests/test_transport.py` | Create | Unit tests for all producer/consumer implementations. `fakeredis[aioredis]` for Redis mocking |

### Key Design
- `EventProducer` Protocol: `put_async(event)`, `put_sync(event)`, `shutdown()`
- `EventConsumer` Protocol: `read_batch(count, block_ms)`, `ack(message_ids)`, `shutdown()`
- One Redis stream per execution (`kodo:events:{fid}`), consumer groups enable multiple spoolers
- `RedisStreamProducer` creates clients lazily, is pickle-safe for Ray serialization
- `XREADGROUP` with blocking replaces the 0.25s polling loop
- Default `EVENT_TRANSPORT=ray` — zero change for existing users

---

## PR 3: Temporal Durable Execution Layer

**Branch:** `feat/temporal-durability`
**Merge order:** Third (conceptually depends on PR 2's transport abstraction)

### Changes

| File | Action | What |
|------|--------|------|
| `pyproject.toml` | Edit | Add `temporal = ["temporalio>=1.7.0"]` to optional-dependencies |
| `kodosumi/config.py` | Edit | Add `EXECUTION_MODE: str = "direct"`, `TEMPORAL_HOST`, `TEMPORAL_NAMESPACE`, `TEMPORAL_TASK_QUEUE`, `TEMPORAL_WORKFLOW_ID_PREFIX`, `TEMPORAL_EXECUTION_TIMEOUT` |
| `kodosumi/workflows.py` | Create | `AgentWorkflow` (workflow.defn) with run/pause/resume/cancel signals and get_status query. `AgentJobInput`/`AgentJobResult` dataclasses |
| `kodosumi/activities.py` | Create | `execute_agent` activity — wraps `create_runner()`, monitors `is_active()`, sends heartbeats every 5s |
| `kodosumi/temporal_worker.py` | Create | Worker process: connects to Temporal, registers workflows+activities, calls `worker.run()` |
| `kodosumi/runner/main.py` | Edit | `Launch()` branches: if `EXECUTION_MODE == "temporal"`, calls `_launch_temporal()` which starts `AgentWorkflow` via Temporal client |
| `kodosumi/cli.py` | Edit | Add `koco temporal-worker` command |
| `tests/test_temporal.py` | Create | Workflow unit tests using `temporalio.testing.WorkflowEnvironment` with time-skipping |

### Key Design
- **Activity wraps Runner, doesn't replace it** — `execute_agent()` calls `create_runner()` exactly as `Launch()` does today. All event streaming, forms, locks work identically.
- Temporal adds durability **around** execution boundary, not inside it
- `AgentWorkflow.run()` executes activity with 3 retries, 120s heartbeat timeout
- Signals (pause/resume/cancel) map to existing Lock mechanism
- Queries (get_status, is_paused) enable status polling without Kodosumi's DB
- Default `EXECUTION_MODE=direct` — zero change for existing users

---

## PR 4: Docker Compose Development Environment

**Branch:** `feat/docker-compose` — Addresses issue #11
**Merge order:** Last (references all 3 prior PRs)

### Changes

| File | Action | What |
|------|--------|------|
| `Dockerfile` | Create | Python 3.11-slim, installs `kodosumi[postgres,redis,temporal]`, exposes 3370 |
| `docker-compose.yml` | Create | Full stack: panel + spooler + ray-head + PostgreSQL + Redis. Temporal behind `--profile temporal` |
| `docker-compose.dev.yml` | Create | Lightweight: kodosumi + ray-head only (SQLite defaults, code mount for reload) |
| `docker-compose.override.yml.example` | Create | Template for custom secrets/config |
| `.dockerignore` | Create | Exclude .git, __pycache__, data/, .env |
| `docs/docker.md` | Create | Quick start, full stack, Temporal profile, configuration guide |

### Key Design
- `docker compose up` = panel + spooler + ray + postgres + redis (no Temporal)
- `docker compose --profile temporal up` adds Temporal server + UI + worker
- `docker compose -f docker-compose.dev.yml up` = minimal dev setup
- Health checks on postgres and redis for proper startup ordering
- Volumes for data persistence across restarts

---

## PR Dependencies

```
PR 1 (PostgreSQL) ──┐
                    ├── PR 4 (Docker)
PR 2 (Redis) ──────┤
       │            │
       └── PR 3 (Temporal) ─┘
```

**Merge order:** PR 1 → PR 2 → PR 3 → PR 4 (PRs 1 and 2 are independent and can be reviewed in parallel)

---

## Verification

For each PR:
1. `pip install -e ".[postgres,redis,temporal]"` — all optional deps install cleanly
2. `pytest` — all existing tests pass with default config (SQLite + Ray polling + direct execution)
3. `pytest tests/test_postgres.py tests/test_transport.py tests/test_temporal.py` — new tests pass

Integration smoke test (after all PRs):
1. `docker compose up` — all services start, panel at http://localhost:3370
2. Register a sample agent flow via the admin panel
3. Execute it — verify events flow through Redis streams to PostgreSQL
4. `docker compose --profile temporal up` — repeat with durable execution
5. Kill the temporal worker mid-execution — verify Temporal retries and completes the job

---

## Files NOT Modified

These core files remain untouched to minimize review burden:
- `kodosumi/serve.py` (ServeAPI — user-facing API unchanged)
- `kodosumi/service/auth.py`, `jwt.py`, `role.py` (auth unchanged)
- `kodosumi/service/flow.py`, `proxy.py` (routing unchanged)
- `kodosumi/service/health.py` (health checks — may add Redis/Temporal status in future PR)
- `kodosumi/runner/formatter.py`, `files.py` (output formatting unchanged)
- `kodosumi/response.py`, `error.py`, `log.py` (utilities unchanged)
- All admin panel templates/static assets
