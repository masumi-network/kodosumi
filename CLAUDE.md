# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kodosumi is a Python framework for executing and orchestrating agentic services at scale. It combines:
- **Ray Serve** for distributed execution of agent workflows
- **Litestar** for the admin panel/API server
- **FastAPI** (via `ServeAPI`) for user-defined agent endpoints deployed on Ray

## Commands

```bash
# Install in development mode
pip install -e ".[tests]"

# Start the admin panel with spooler (production)
koco start

# Start with custom address
koco start --address http://0.0.0.0:8080

# Start development server with auto-reload
koco serve --reload

# Start/stop spooler independently
koco spool --start
koco spool --stop
koco spool --status

# Start Ray cluster (required before deployment)
dotenv run -- ray start --head

# Run tests
pytest tests/
pytest tests/test_flow.py -v  # single test file
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `koco start` | Start admin panel with spooler (production mode) |
| `koco serve` | Start development server (use `--reload` for auto-reload) |
| `koco spool` | Manage spooler independently (`--start`, `--stop`, `--status`) |

**Note:** Deployments are managed through the Admin Panel UI (`/admin/expose/`) or the `/boot/` API endpoints. The legacy `koco deploy` command has been removed.

## Architecture

### Two-Server Design
1. **Admin Panel (Litestar)** - `kodosumi/service/app.py`
   - User authentication, dashboard, execution monitoring
   - Expose management and deployment control
   - Proxies requests to deployed agent services
   - Default port: 3370

2. **Agent Services (FastAPI on Ray Serve)** - User-defined via `ServeAPI`
   - Define agent endpoints using `@app.enter()` decorator
   - Launched via `Launch()` which creates Ray actors
   - Deployed via Expose/Boot system (Admin UI or API)

### Core Components
- `kodosumi/core.py` - Public API exports (`ServeAPI`, `Launch`, `Tracer`, etc.)
- `kodosumi/serve.py` - `ServeAPI` class (extended FastAPI for agent endpoints)
- `kodosumi/runner/main.py` - `Runner` Ray actor that executes agent workflows
- `kodosumi/runner/payment.py` - `MasumiClient` for blockchain payment integration
- `kodosumi/spooler.py` - Background process that collects execution events from Ray queues
- `kodosumi/config.py` - Settings with `KODO_` env prefix (e.g., `KODO_EXEC_DIR`, `KODO_MASUMI`)
- `kodosumi/service/expose/` - Expose management system:
  - `control.py` - API endpoints for CRUD, registry, wallets, exchange, audit
  - `boot.py` - Boot/shutdown streaming endpoints
  - `db.py` - SQLite database operations for expose items
  - `models.py` - Pydantic models for expose data
  - `registry.py` - Masumi on-chain registry client (register, deregister, status)
- `kodosumi/service/sumi/` - Sumi Protocol (MIP-002/MIP-003):
  - `control.py` - External API for job submission, status, HITL locks
  - `models.py` - MIP-003 compliant request/response models
  - `schema.py` - OpenAPI to MIP-003 schema conversion
- `kodosumi/service/dashboard.py` - Analytics API (running agents, errors, timeline, stats)
- `kodosumi/service/role.py` - User management and profile endpoint

### Data Flow
1. User submits form on agent endpoint → `ServeAPI` handles POST
2. `Launch()` creates a `Runner` actor on Ray with detached lifetime
3. Runner executes the entry point function with a `Tracer` for logging
4. Spooler polls Ray queues, writes events to per-execution SQLite databases
5. Admin panel reads execution data for monitoring/display

### Directory Structure
```
data/
├── admin.db              # User database (SQLAlchemy)
├── expose.db             # Expose items database
├── serve_config.yaml     # Ray Serve configuration
├── audit.log             # Boot/deployment audit log (rotating)
├── app.log               # Application server log (rotating)
├── spooler.log           # Spooler log (rotating)
├── uploads/              # Temporary file uploads
├── config/
│   └── config.yaml       # Base deployment configuration
├── execution/            # Per-user execution data
│   └── {user_id}/
│       └── {exec_id}/
│           ├── sqlite3.db  # Execution event log (monitor table)
│           ├── in/         # Uploaded input files
│           └── out/        # Output files
```

### API Endpoints

#### Expose Management (`/expose/`)
- `GET /expose/` - List all expose items
- `GET /expose/{name}` - Get single expose item
- `POST /expose/` - Create or update expose item
- `DELETE /expose/{name}` - Delete expose item
- `POST /expose/health` - Health check all exposes
- `POST /expose/{name}/health` - Health check single expose

#### Boot/Deployment (`/boot/`)
- `POST /boot/` - Start Ray Serve deployment (streaming)
- `GET /boot/` - Get boot status
- `GET /boot/stream` - Subscribe to boot message stream
- `DELETE /boot/` - Shutdown Ray Serve (streaming)
- `POST /boot/refresh/{name}` - Refresh single expose

#### Import/Export (`/exchange/`)
- `GET /exchange/export` - Export all expose items to JSON
- `POST /exchange/import` - Import expose items from JSON

#### Registry (`/expose/{name}/registry`)
- `GET /expose/{name}/registry` - Get on-chain registration status
- `POST /expose/{name}/registry` - Register agent on Masumi
- `POST /expose/{name}/registry/poll` - Poll for confirmation
- `POST /expose/{name}/registry/deregister` - Deregister agent
- `GET /expose/{name}/wallets` - List selling wallets

#### Sumi Protocol (`/sumi/`)
- `GET /sumi/` - List available services
- `POST /sumi/{expose}/{meta}/start_job` - Start a job
- `GET /sumi/{expose}/{meta}/status/{job_id}` - Get job status
- `POST /sumi/{expose}/{meta}/provide_input` - HITL input
- `GET /sumi/{expose}/{meta}/input_schema` - Get input schema

#### Profile & Roles
- `GET /role/profile` - Get own user profile (no operator required)
- `GET /role/` - List all roles (operator only)

#### Other Key Endpoints
- `GET /health/` - System health (Ray, Serve, Spooler, Services)
- `POST /flow/register` - Register flows from OpenAPI endpoints
- `GET /flow/` - List registered flows
- `GET /api/dashboard/*` - Analytics (running agents, errors, timeline, stats)

## Key Patterns

### Creating Agent Endpoints
```python
from kodosumi.core import ServeAPI, Launch, Tracer
from kodosumi.service.inputs.forms import Model, InputText, Submit

app = ServeAPI()

@app.enter("/", model=Model(InputText(name="query"), Submit("Run")))
async def my_agent(inputs: dict, request):
    return Launch(request, "mymodule:agent_function", inputs=inputs)

async def agent_function(inputs: dict, tracer: Tracer):
    await tracer.action("Processing...")
    result = do_work(inputs)
    await tracer.result(result)
    return result
```

### Monitor Table Event Types
- `status`: Plain string ("running", "finished", "error") - NOT JSON
- `meta`: JSON object with execution metadata
- `error`: Can be plain text or JSON - handle both
- `inputs`, `result`, `action`, `final`: Usually JSON

### Configuration
Settings via environment variables with `KODO_` prefix or `.env` file:
- `KODO_EXEC_DIR` - Execution data directory
- `KODO_APP_SERVER` - Admin panel URL
- `KODO_RAY_SERVER` - Ray cluster address
- `KODO_RAY_SERVE_ADDRESS` - Ray Serve HTTP endpoint (default: `http://localhost:8005`)
- `KODO_BOOT_HEALTH_TIMEOUT` - Boot process timeout in seconds (default: 1800)
- `KODO_ADMIN_EMAIL` / `KODO_ADMIN_PASSWORD` - Default admin credentials
- `KODO_SUMI_ADDRESS` - Public Sumi API URL (fallback to `APP_SERVER`)
- `KODO_MASUMI` - Masumi payment config: `"Name URL Token [pay_by_time] [submit_result_by_time] [poll_interval]"`
- `KODO_MASUMI0..9` - Additional Masumi network configs (multi-network support)

### Litestar Controllers
```python
from litestar import Controller, get
from litestar.datastructures import State

class MyAPI(Controller):
    @get("/endpoint")
    async def handler(self, state: State) -> dict:
        exec_dir = Path(state["settings"].EXEC_DIR)
        # ...
```

## Code Style
- Async-first for all I/O operations
- Type hints required for function signatures
- Use `Path` objects with `/` operator for paths
- Use `aiosqlite` for execution database access
- Frontend: Jinja2 templates + Beer CSS + vanilla JS + D3.js
- never run all pytests. these run very long and shall be launched by the user only.
- only run pytests you created your self