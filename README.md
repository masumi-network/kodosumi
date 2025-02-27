# >kodosumi

> [!NOTE]
>
> This is an early development version of kodosumi.

kodosumi is a runtime environment to manage and execute agentic services at scale. The system is based on [ray](https://ray.io) - a distributed computing framework - and a combination of [litestar](https://litestar.dev/) and [fastapi](https://fastapi.tiangolo.com/) to deliver men/machine interaction.

kodosumi is one component of a larger ecosystem with [masumi and sokosumi](https://www.masumi.network/).

# introduction

kodosumi consists of three building blocks:

1. The ray cluster to execute agentic services at scale.
2. The kodosumi web interface and API services.
3. The kodosumi spooler monitoring agentic service execution and results.

# installation

This installation has been tested with versions `ray==2.43.0` and `python==3.12.6`.

### STEP 1 - clone and install kodosumi.

```bash
git clone https://github.com/masumi-network/kodosumi.git
cd kodosumi
git checkout dev
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### STEP 2 - start ray as a daemon.

```bash
ray start --head
```

Check ray status with `ray status` and visit ray dashboard at [http://localhost:8265](http://localhost:8265). For more information about ray visit [ray's documentation](https://docs.ray.io/en/latest).

### STEP 3 - start kodosumi spooler.

```bash
python -m kodosumi.cli spool
```

### STEP 4 - create _ray serve_ configuration
Deploy the example services available in folder `./tests`. You need to create a _ray serve_ configuration file.

```yaml
# ./config.yaml

proxy_location: EveryNode
http_options:
  host: 127.0.0.1
  port: 8001
grpc_options:
  port: 9001
  grpc_servicer_functions: []
logging_config:
  encoding: TEXT
  log_level: DEBUG
  logs_dir: null
  enable_access_log: true
applications:
- name: app
  route_prefix: /counter
  import_path: app.main:fast_app
  runtime_env:
    env_vars:
      PYTHONPATH: ./tests
- name: hymn
  route_prefix: /hymn
  import_path: app.hymn:fast_app
  runtime_env:
    env_vars:
      PYTHONPATH: ./tests
      OPENAI_API_KEY: <- enter your key here ->
      EXA_API_KEY:  <- enter your key here ->
      SERPER_API_KEY:  <- enter your key here ->
      OTEL_SDK_DISABLED: "true"
    pip:
    - crewai==0.86.*
    - crewai_tools==0.17.*
- name: Job Posting
  route_prefix: /job_posting
  import_path: app2.app:fast_app
  runtime_env:
    env_vars:
      PYTHONPATH: ./tests
      OPENAI_API_KEY: <- enter your key here ->
      EXA_API_KEY:  <- enter your key here ->
      SERPER_API_KEY:  <- enter your key here ->
      OTEL_SDK_DISABLED: "true"
      LITELLM_LOG: DEBUG
    pip:
    - crewai==0.86.*
    - crewai_tools==0.17.*
```
Update the required API keys for [openai](https://openai.com/), [Exa search engine](https://docs.exa.ai/reference/getting-started) and [Serper Google Search](https://serper.dev/). These tools will be used by the deployed [CrewAI](https://www.crewai.com/) services.

Save the file `config.yaml` in your kodosumi repository folder.

### STEP 5 - deploy with `ray serve`
 
 Open a new terminal. Start the deployment but do not forget to activate the Python virtual environment beforehand.

```bash
source .venv/bin/activate
serve deploy ./config.yaml 
```

Depending on your local resources this deployment will take a couple of minutes because service dependencies will be installed by ray. Visit [ray's dashboard pane _serve_](http://localhost:8265/#/serve), follow the process and verify all three services have been successfully deployed.

All services should be **RUNNING** and all deployments should be **HEALTHY** at the end.

### STEP 6 - start kodosumi services

Finally start and visit kodosumi services at [http://localhost:3370](http://localhost:3370). 


```bash
python -m kodosumi.cli serve
```

### STEP 7 - finish

Login with test user `admin` (password `admin`).

If all went well, then you see three agentic test services:

1. [Test App](./tests/app/main.py) (a simple runner for testing purposes)
2. [Hymn Creator](./tests/app/hymn.py)
3. [Job Posting Creator](./tests/app2/app.py)

Stop the kodosumi services and spooler by hitting `CNTRL+C` in the corresponding terminal. Stop the ray daemon with command `ray stop`.

# development notes

These development notes provide an overview of creating a web app using FastAPI and Ray Serve, alongside an agentic service using `crewai`. 

```python
# ./tests/app/hymn.py

from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from ray.serve import deployment, ingress

from kodosumi.serve import Launch, ServeAPI


app = ServeAPI()  # instead of FastAPI !!!

templates = Jinja2Templates(
    directory=Path(__file__).parent.joinpath("templates"))

@deployment
@ingress(app)
class HymnTest:

    @app.get("/", 
             name="Hymn Generator", 
             description="Creates a short hymn using openai and crewai.")

    async def get(self, request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request, name="hymn.html", context={})

    @app.post("/", response_model=None)
    async def post(self, request: Request):
        form_data = await request.form()
        topic: str = str(form_data.get("topic", ""))
        if topic.strip():
            return Launch(request, "app.crew:crew", {"topic": topic})
        return await self.get(request)

fast_app = HymnTest.bind()
```

Component [`kodosumi.serve.ServeAPI`](./kodosumi/serve.py#ServeAPI) inherits from `fastapi.FastAPI` and represents the deployment (`@deployment`) and entry point (`@ingress`) for ray serve. The deployment implements one endpoint `/` with methods `GET` and `POST`. For user interaction the `GET` method delivers a form (see template [`hymn.html`](./tests/app/templates/hymn.html)). The `POST` method consumes the form data and launches _CrewAI_ object `crew` in module [`app.crew`](./tests/app/crew.py) in folder [`tests`](./tests/). Please note that the `config.yaml` file includes the [`./tests`](./tests/) directory as a `PYTHONPATH`.

# further reads

* ray serve
  * [getting started](https://docs.ray.io/en/latest/serve/getting_started.html)
  * [set up FastAPI with ray](https://docs.ray.io/en/latest/serve/http-guide.html) - **IMPORTANT:** you will have to use `kodosumi.serve.ServeAPI` instead of `FastAPI` to use kodosumi.
  * [development workflow with ray](https://docs.ray.io/en/latest/serve/advanced-guides/dev-workflow.html)
  * [serve config files](https://docs.ray.io/en/latest/serve/production-guide/config.html)

# disclaimer

kodosumi is under development. The kodosumi web app is a simple admin panel. There are still many features missing or not working. All bug reports and feature requests are much appreciated.

The following list provides a first glimpse of upcoming features.

* improve and harden interaction between ray and kodosumi spooler on long-running agentic services
* kill and remove jobs
* authentication and user/role management
* improve the admin panel
* agentic service registry for agent-to-agent interaction
* result formatting
* cluster and job monitoring
* much and many more
