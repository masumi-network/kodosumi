# >kodosumi

> [!NOTE]
>
> This is an early development version of kodosumi.

kodosumi is a runtime environment to manage and execute agentic services at scale. The system is based on [ray](https://ray.io) - a distributed computing framework - and a combination of [litestar](https://litestar.dev/) and [fastapi](https://fastapi.tiangolo.com/) to deliver men/machine interaction.

kodosumi is one component of a larger ecosystem with [masumi and sokosumi](https://www.masumi.network/).


# introduction

kodosumi consists of three main building blocks:

1. The Ray cluster to execute agentic services at scale.
2. The kodosumi web interface and API services.
3. Agentic Services delivered through kodosumi and executed through Ray.


# installation

This installation has been tested with versions `ray==2.44.1` and `python==3.12.6`.

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


### STEP 3 - prepare environment

To use [openai](https://openai.com/) API you need to create a local file `.env` to define the following API keys:

```
OPENAI_API_KEY=...
EXA_API_KEY=...
SERPER_API_KEY=...
```


### STEP 4 - deploy example apps with `ray serve`

Deploy the example services available in folder `./apps`. Use file `apps/config.yaml`.

```bash
serve deploy apps/config.yaml
```


### STEP 5 - start kodosumi

Finally start the kodosumi components and register ray endpoints available at 
[http://localhost:8001/-/routes](http://localhost:8001/-/routes).


```bash
koco start --register http://localhost:8001/-/routes
```

Please be patient if the Ray serve applications take a while to setup, install and deploy. Follow progress in the [Ray Dashboard](http://localhost:8265). On my laptop initial deployment takes three to four minutes.


### STEP 6 - Look around

Visit kodosumi admin panel at [http://localhost:3370](http://localhost:3370). The default user is defined in `config.py` and reads `name=admin` and `password=admin`. If one or more Ray serve applications are not yet available when kodosumi starts, you need to refresh the list of registered flows. Visit **Routes Screen** at [(http://localhost:3370/admin/routes](http://localhost:3370/admin/routes) in the **Admin Panel** at [http://localhost:3370/admin/flow](http://localhost:3370/admin/flow). See also the **OpenAPI documents with Swagger** [http://localhost:3370/schema/swagger](http://localhost:3370/schema/swagger). 

If all went well, then you see a couple of test services. Be aware you need some OpenAPI, Exa and Serper API keys if you want to use all Agentic Services.

Stop the kodosumi services and spooler by hitting `CNTRL+C` in the corresponding terminal. Stop Ray _serve_ with `serve shutdown --yes`. Stop the ray daemon with command `ray stop`.


# development notes

These development notes provide an overview of creating a web app using FastAPI and Ray Serve, alongside an agentic service using `crewai`. 


```python
import sys
from pathlib import Path
from typing import Annotated

import uvicorn
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ray import serve
from kodosumi.serve import Launch, ServeAPI, Templates


class HymnRequest(BaseModel):
    topic: str


app = ServeAPI()

templates = Templates(
    directory=Path(__file__).parent.joinpath("templates"))

@app.get("/", summary="Hymn Creator",
            description="Creates a short hymn using openai and crewai.",
            version="1.0.0",
            author="m.rau@house-of-communication.com",
            tags=["CrewAI", "Test"],
            entry=True)
async def get(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request, name="hymn.html", context={})

@app.post("/", entry=True)
async def post(request: Request, 
                data: Annotated[HymnRequest, Form()]) -> Response:
    return Launch(request, "apps.example3:crew", data, reference=get)


@serve.deployment
@serve.ingress(app)
class Example1: pass


fast_app = Example1.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example3:app", host="0.0.0.0", port=8004, reload=True)
```

Component [`kodosumi.serve.ServeAPI`](./kodosumi/serve.py#ServeAPI) inherits from `fastapi.FastAPI` and is the application servicing one endpoint with `GET` and `POST /`. Method `get` returns a Jinja2 template. HTML file `hymn.html` defines a form which follows pydantic model `HymnRequest`. With _form submission_ the request, the entrypoint (`apps.xample3:crew`), and the annotated form `data` is passed to `Launch` the crew. This crew is defined in package `apps.example3` as object `crew`.

The application in `apps.example3` is not in scope of `apps/config.yaml`. To launch the application with **uvicorn** follow the steps:

```bash
ray start --head
python -m apps.example3
# in a new terminal
koco serve --register http://localhost:8004/openapi.json
```

If you prefer to launch the application with Ray you can either run or deploy it to a running Ray cluster.

```bash
ray start --head
serve deploy apps.example3:fast_app
koco serve --register http://localhost:8000/-/routes
```

# further reads

* ray serve
  * [getting started](https://docs.ray.io/en/latest/serve/getting_started.html)
  * [set up FastAPI with ray](https://docs.ray.io/en/latest/serve/http-guide.html) - **IMPORTANT:** you will have to use `kodosumi.serve.ServeAPI` instead of `FastAPI` to use kodosumi.
  * [development workflow with ray](https://docs.ray.io/en/latest/serve/advanced-guides/dev-workflow.html)
  * [serve config files](https://docs.ray.io/en/latest/serve/production-guide/config.html)
