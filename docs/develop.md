# kodosumi development workflow

This guide provides a step-by-step approach to implementing, exposing, and deploying an agentic service using kodosumi.

## overview

The development process with kodosumi consists of two main work streams:

1. **Implementing the Entrypoint**

   The entrypoint serves as the foundation of your service, housing the core business logic. It acts as the central hub for distributed computing, where complex calculations are broken down and distributed across multiple processing units using Ray. This component is responsible for orchestrating parallel tasks and ensuring efficient resource utilization.

2. **Implementing the Endpoint**

   The endpoint establishes the HTTP interface for user interaction, providing a structured way to receive and process user input. It implements comprehensive input validation and manages the entire service lifecycle. This component is crucial for launching and monitoring the execution flow of your service.

Together, these components form a robust architecture that enables the creation of scalable and efficient agentic services. The entrypoint handles the computational complexity, while the endpoint ensures reliable user interaction and service management.

## step-by-step implementation guide

We start implementing an example service with the folder package structure and the build of the _calculator_. To keep this example dead simple we create an agentic service which does nothing more than waiting. 

You can visit final implementation of this example service at [./apps/my_service/app.py](../apps/my_service/app.py) and [./apps/my_service/calc.py](../apps/my_service/calc.py)

### 1. setup project structure

Create a new directory `./home` to host the source code of your agentic service. Create a sub folder with the name of your service.

```bash
mkdir home/my_service
touch home/my_service/__init__.py
touch home/my_service/calc.py
touch home/my_service/app.py
```

> [!NOTE]
> In production each service represents a Ray _serve deployment_ with its own runtime environment including Python dependencies and environment settings.

### 2. implement entrypoint

We start development with the entrypoint. To keep this example as simple as possible we implement an entrypoint `execute` and a remote execution method `calculate` to simulate some heavy lifting. Implement both methods in `calc.py`.

```python
# ./home/my_service/calc.py
import asyncio
import ray
from kodosumi.core import Tracer

async def execute(inputs: dict, tracer: Tracer):
    tasks = inputs["tasks"]
    futures = [calculate.remote(i) for i in range(tasks)]
    pending = futures.copy()
    total = []
    while True:
        done, pending = ray.wait(pending, timeout=0.01)
        if done:
            ret = await asyncio.gather(*done)
            total.extend(ret)
            await tracer.markdown(f"have results `{ret}`")
            print(f"have result `{len(total)/tasks*100:.2f}%`")
        if not pending:
            break
        await asyncio.sleep(0.1)
    return total
```

We will extend this method later with some tracing and results trackimg. For now it is important to understand that this entrypoint utilises a method `calculate` to create futures which actually do the job. 

    futures = [calculate.remote(i, tracer) for i in range(tasks)]

The implementation of this method `calculate` is a mock. Nevertheless we let Ray distribute the jobs to it's workers:

```python
# ./home/my_service/calc.py
import time
from random import randint
import datetime

@ray.remote
def calculate(i: int):
    # do your heavy-lifting work here
    t0 = datetime.datetime.now()
    s = randint(1, 3)
    time.sleep(s)
    t1 = datetime.datetime.now()
    return {
        "i": i, 
        "t0": t0, 
        "t1": t1, 
        "delta": (t1 - t0).total_seconds(), 
        "result": s
    }
```

Watch the Ray decorator `@remote` which instructs Ray to remote execute this method in the Ray cluster. 

See the complete implementation of the _calculation_ in  [./apps/my_service/calc.py](../apps/my_service/calc.py).

### 3. setup app

We now proceed to setup the app with an endpoint to interact with your entrypoint. In `app.py`, set up the basic application structure:

```python
# ./apps/my_service/app.py
from kodosumi.core import ServeAPI

app = ServeAPI()
```

The `ServeAPI()` initialization creates a FastAPI application with kodosumi-specific extensions. It provides automatic OpenAPI documentation, error handling, authentication and access control, input validation, and some configuration management.

The `app` instance will be used to define the service _endpoint_ with `@app.enter` and to define service meta data following OpenAPI standards. We will do this in step **5** of this guide. Before we specify the inputs model with next step **4**.

### 4. define user interface

Before we implement the endpoint we will define the user interface of your service. We import _forms_ elements from `kodosumi.core`. See [forms overview](./forms.md) on the supported form and input elements.

```python
from kodosumi.core import forms as F

my_model = F.Model(
    F.Markdown("""
    # My Service
    Specify the number of tasks to run. Each tasks will take between 1 and 3 
    seconds to complete. The level of concurrency is determined by the size of
    the Ray cluster.
    """),
    F.Break(),
    F.InputText(label="Number of Tasks", name="tasks", value="25"),
    F.Submit("Submit"),
    F.Cancel("Cancel")
)
```

A simple form is rendered that displays a headline **My Service** with some introductionary text, followed by a text input field for the number of tasks (with default value 25) and two buttons to submit or cancel the request.

### 5. implement endpoint

Implement the HTTP endpoint using the `@enter` decorator of the `ServeAPI` instance `app`. We will attach the input model defined in the previous step and declare key OpenAPI and extra properties (_summary_, _description_, and _tags_, _version_, _author_ for example).

```python
import fastapi
from kodosumi.core import InputsError
from kodosumi.core import Launch

@app.enter(
    path="/",
    model=my_model,
    summary="My Service",
    description="Not so agentic example service.",
    tags=["Test"],
    version="1.0.0",
    author="your.email@domain.com")
async def enter(request: fastapi.Request, inputs: dict):
    tasks = inputs.get("tasks", 0)
    error = InputsError()
    if not tasks:
        error.add(tasks="Please specify the number of concurrent tasks.")
    if isinstance(tasks, str):
        tasks = int(tasks)
    MAX = 500
    if tasks > MAX:
        error.add(tasks=f"Maximum number of concurrent tasks is {MAX}")
    if error.has_errors():
        raise error
    return Launch(
        request, 
        "my_service.calc:execute", 
        inputs={"tasks": tasks}
    )
```

### 6. configure Ray deployment

Add the deployment configuration at the end of your `app.py`. 

```python
from ray import serve

@serve.deployment
@serve.ingress(app)
class MyService: pass

fast_app = MyService.bind()
```

See [Configure Ray Serve Deployments](https://docs.ray.io/en/latest/serve/configure-serve-deployment.html) for additional options on your deployment. Be advised to gather some experience with Ray core components before you rollout your services. Understand [remote resource requirements](https://docs.ray.io/en/latest/ray-core/scheduling/resources.html#resource-requirements) and how to [limit concurrency to avoid OOM issues](https://docs.ray.io/en/latest/ray-core/patterns/limit-running-tasks.html#pattern-using-resources-to-limit-the-number-of-concurrently-running-tasks)

### 7. run and deploy

#### with Ray

You can now run and deploy your service with `serve run my_service.app:fast_app` or `serve deploy apps.my_service.app:fast_app`. Ray reports available routes on port http://localhost:8000/-/routes. Register these Ray serve deployments with

    cd ./home
    koco start --register http://localhost:8000/-/routes

Retrieve the inputs scheme from [/-/localhost/8000/-/](http://localhost:3370/-/localhost/8000/-/), and test the service at [/inputs/-/localhost/8000/-/](http://localhost:3370/inputs/-/localhost/8000/-/).

Use for example `curl` to POST a service requests after successful authentication:

    curl -b cookie -c cookie -X POST -d '{"name": "admin", "password": "admin"}' http://localhost:3370/api/login
    curl -b cookie -c cookie -X POST -d '{"tasks": 100}' http://localhost:3370/-/localhost/8000/-/

#### with uvicorn

Debugging Ray jobs and serve deployments requires remote debugger setup (see [Distributed debugging with](https://docs.ray.io/en/latest/ray-observability/ray-distributed-debugger.html)). To debug your `ServeAPI` application you can run and deploy your service with **uvicorn instead of Ray serve**. 

Either launch uvicorn directly with `uvicorn my_service.app:app --port 8005` or extend file `./apps/my_service/app.py` with the following `__main__` section. Then invoke the module with `python -m apps.my_service.app`.

```python
if __name__ == "__main__":
    sys.path.append(str(Path(__file__).parent.parent.parent))
    uvicorn.run("apps.my_service.app:app", 
                host="0.0.0.0", 
                port=8005, 
                reload=True)
```

Both approaches start a uvicorn application server at the specified port `8005` and deliver the OpenAPI scheme at [http://localhost:8005/openapi.json](http://localhost:8005/openapi.json). Feed this API scheme into kodosumi panel either via the [config screen](http://localhost:3370/admin/routes) or via command line and at startup:

    koco serve --register http://localhost:8005/openapi.json

> [!NOTE]
> You can `--register` multiple agentic services by using the parameter multiple times.

Continue with background information on [execution lifecycle and the `Launch object`](./lifecycle.md). Further examples can be found in folder `./apps`:

- `apps/my_serve/app.py` for a simple calculation service
- `apps/prime/app.py` for a simple calculation service
- `apps/form/app.py` for form handling examples
- `apps/throughput/app.py` for performance testing
- `apps/hymn/app.py` for AI integration examples
