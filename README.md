<div align="center">
    <img src="./docs/logo/favicon.png" width="96px" alt="Kodosumi logo"/>
    <h1>Kodosumi</h1>
    <p><strong>Execute and orchestrate agentic AI services at scale</strong></p>
    <p>
        <a href="https://pypi.org/project/kodosumi/"><img src="https://img.shields.io/pypi/v/kodosumi?color=C4FE0A&label=PyPI" alt="PyPI"></a>
        <a href="https://github.com/masumi-network/kodosumi/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License"></a>
        <a href="https://docs.kodosumi.io"><img src="https://img.shields.io/badge/Docs-docs.kodosumi.io-green" alt="Docs"></a>
        <a href="https://discord.com/invite/aj4QfnTS92"><img src="https://img.shields.io/badge/Discord-join-7289DA?logo=discord&logoColor=white" alt="Discord"></a>
    </p>
</div>

---

Kodosumi is a Python framework for running AI agent workflows in production. It combines **Ray Serve** for distributed execution, a built-in **Admin Panel** for monitoring, and **Masumi** blockchain integration for paid agent services.

## Quick Start

```bash
pip install kodosumi
```

### 1. Create an Agent

```python
# app.py
import kodosumi.core as core
from kodosumi.core import forms as F

app = core.ServeAPI()

@app.enter(
    path="/",
    model=F.Model(
        F.InputText(label="Question", name="question"),
        F.Submit("Run"),
    ),
    summary="My First Agent",
    tags=["Demo"],
)
async def enter(request, inputs: dict):
    return core.Launch(request, "app:run_agent", inputs=inputs)

async def run_agent(inputs: dict, tracer: core.Tracer):
    question = inputs.get("question", "")
    await tracer.result({"answer": f"You asked: {question}"})
    return {"answer": f"You asked: {question}"}
```

### 2. Start Development Server

```bash
# Start Ray (required once)
ray start --head

# Start Kodosumi admin panel
koco serve

# In another terminal, deploy your agent
serve run app:app --host 0.0.0.0 --port 8001
```

Open [http://localhost:3370](http://localhost:3370) to see your agent in the Admin Panel.

### 3. Deploy to Production

Agents are managed through the **Expose** system in the Admin Panel:

1. Create an Expose entry with your agent's config (YAML)
2. Click **Boot** to deploy all configured agents to Ray Serve
3. Monitor executions in the **Timeline** and **Analytics** dashboards

## Features

- **Distributed Execution** - Scale from laptop to cluster with Ray Serve
- **Admin Panel** - Monitor agents, view timelines, track errors
- **Payment Dashboard** - Revenue tracking and failure analysis for Masumi-integrated agents
- **Forms & Validation** - Declarative input forms with client-side validation
- **File Upload/Download** - Chunked file transfer for agent I/O
- **Human-in-the-Loop** - Lock/lease pattern for interactive agent workflows
- **MIP-003 Protocol** - Standard API for agent discovery and job management (Sumi endpoints)
- **Blockchain Payments** - Masumi/Cardano integration for paid agent services

## Architecture

```
                    +------------------+
                    |   Admin Panel    |  Litestar (port 3370)
                    |   - Dashboard    |  Monitoring, Expose, Analytics
                    |   - Masumi       |  Payment tracking
                    +--------+---------+
                             |
                    +--------+---------+
                    |    Ray Serve     |  Distributed execution
                    |  +-----+ +-----+|
                    |  |App 1| |App 2||  Your agent endpoints
                    |  +-----+ +-----+|
                    +--------+---------+
                             |
                    +--------+---------+
                    |     Spooler      |  Event collection
                    |  SQLite per job  |  Execution logs
                    +------------------+
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `koco serve` | Start admin panel (development) |
| `koco serve --reload` | Start with auto-reload |
| `koco start` | Start admin panel + spooler (production) |
| `koco spool --start` | Start spooler independently |
| `koco spool --status` | Check spooler status |

## Configuration

All settings via environment variables with `KODO_` prefix or `.env` file:

```bash
KODO_APP_SERVER=http://localhost:3370   # Admin panel URL
KODO_RAY_SERVER=localhost:6379          # Ray cluster address
KODO_EXEC_DIR=./data/execution          # Execution data directory
KODO_ADMIN_EMAIL=admin@example.com      # Default admin credentials
KODO_ADMIN_PASSWORD=admin
```

See [Configuration Reference](https://docs.kodosumi.io/guides/getting-started/config) for all options.

## Examples

See the [kodosumi-examples](https://github.com/masumi-network/kodosumi-examples) repository for complete examples:

- **simple** - Minimal agent with text input
- **form** - Advanced forms with validation
- **upload** - File upload/download
- **hitl** - Human-in-the-loop with locks
- **prime** - Multi-step workflow

## Documentation

Full documentation at [docs.kodosumi.io](https://docs.kodosumi.io):

- [Installation Guide](https://docs.kodosumi.io/guides/getting-started/installation)
- [Development Workflow](https://docs.kodosumi.io/guides/getting-started/develop)
- [Forms & Inputs](https://docs.kodosumi.io/guides/getting-started/forms)
- [Expose & Deployment](https://docs.kodosumi.io/guides/deploy/expose)
- [API Reference](https://docs.kodosumi.io/api-reference)

## Contributing

```bash
# Clone and install in development mode
git clone https://github.com/masumi-network/kodosumi.git
cd kodosumi
python -m venv .venv
source .venv/bin/activate
pip install -e ".[tests]"

# Run tests
pytest tests/

# Start development server
ray start --head
koco serve --reload
```

## License

[Apache License 2.0](LICENSE)
