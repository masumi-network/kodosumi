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
git checkout feature/candidate
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
koco spool
```

> [!NOTE]
>
> This starts the spooler as a daemon. To stop the spooler daemon run
>
>     koco spool --stop
>
> You can start a blocking spooler instead with
>
>     koco spool --block


### STEP 4 - prepare environment

To use [openai](https://openai.com/) API you need to create a local file `.env` to define the following API keys:

```
OPENAI_API_KEY=...
```


### STEP 5 - deploy with `ray serve`

Deploy the example services available in folder `./apps`. To deploy _example2_ and _example3_ use file `apps/config.yaml`.

```bash
serve deploy apps/config.yaml
```


### STEP 6 - start kodosumi services

Finally start the kodosumi service and register ray endpoints available at 
[http://0.0.0.0:8001/-/routes](http://0.0.0.0:8001/-/routes).


```bash
koco serve --register http://0.0.0.0:8001/-/routes
```

### STEP 7 - finish

Visit kodosumi admin panel at [http://localhost:3370](http://localhost:3370). The default user is defined in `config.py` and reads `name=admin` and `password=admin`.

If all went well, then you see two test services:

3. [Armstrong Numbers Generator](./apps/example2.py)
2. [Hymn Creator](./apps/example3.py)

Stop the kodosumi services and spooler by hitting `CNTRL+C` in the corresponding terminal. Stop Ray _serve_ with `serve shutdown --yes`. Stop the ray daemon with command `ray stop`.

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
