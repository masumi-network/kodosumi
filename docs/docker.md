# Docker Compose Setup

## Quick Start

```bash
# Full stack (panel + spooler + ray + postgres + redis)
docker compose up

# Dev mode (panel + spooler + ray, code mounted for reload)
docker compose -f docker-compose.dev.yml up

# Full stack + Temporal durable execution
docker compose --profile temporal up
```

## Architecture

```
docker-compose.yml (full stack)
├── ray-head         — Ray GCS + Dashboard (:8265)
├── postgres         — PostgreSQL 16 (:5432)
├── redis            — Redis 7 (:6380 → 6379)
├── panel            — Kodosumi web UI + API (:3370)
├── spooler          — Event writer (polls Ray actors)
└── [temporal]       — Optional profile:
    ├── temporal     — Temporal server (:7233)
    ├── temporal-ui  — Temporal web UI (:8233)
    └── temporal-worker — Kodosumi Temporal worker
```

## Configuration

All settings are configured via environment variables with `KODO_` prefix.
Default behavior (SQLite + Ray queues) works out of the box.

### Enable PostgreSQL

```bash
# Copy and edit the override file
cp docker-compose.override.yml.example docker-compose.override.yml
# Uncomment the PostgreSQL settings, then:
docker compose up
```

Or set directly:
```yaml
# docker-compose.override.yml
services:
  panel:
    environment:
      KODO_ADMIN_DATABASE: "postgresql+asyncpg://kodosumi:kodosumi@postgres:5432/kodosumi"
      KODO_EXECUTION_DATABASE: "postgresql+asyncpg://kodosumi:kodosumi@postgres:5432/kodosumi"
```

### Enable Redis Streams

```yaml
services:
  panel:
    environment:
      KODO_EVENT_TRANSPORT: "redis"
      KODO_REDIS_URL: "redis://redis:6379"
  spooler:
    environment:
      KODO_EVENT_TRANSPORT: "redis"
      KODO_REDIS_URL: "redis://redis:6379"
```

### Enable Temporal

```bash
docker compose --profile temporal up
```

This adds the Temporal server, UI, and a kodosumi worker.
The worker automatically connects to Ray and Temporal.

## Volumes

| Volume | Purpose |
|--------|---------|
| `pgdata` | PostgreSQL data (persistent) |
| `redisdata` | Redis data (persistent) |
| `appdata` | Kodosumi execution data + admin DB |

## Ports

| Port | Service |
|------|---------|
| 3370 | Kodosumi panel |
| 5432 | PostgreSQL |
| 6379 | Ray GCS |
| 6380 | Redis |
| 7233 | Temporal (with `--profile temporal`) |
| 8233 | Temporal UI (with `--profile temporal`) |
| 8265 | Ray Dashboard |

## Health Checks

All infrastructure services have health checks:
- **ray-head**: `ray status`
- **postgres**: `pg_isready`
- **redis**: `redis-cli ping`

The panel and spooler wait for their dependencies to be healthy before starting.

## Development

The dev compose file (`docker-compose.dev.yml`) mounts local code:

```bash
docker compose -f docker-compose.dev.yml up
```

- `./kodosumi/` is mounted into the container
- `./data/` is used for SQLite and execution files
- Changes to Python files trigger reload (when `KODO_APP_RELOAD=true`)
