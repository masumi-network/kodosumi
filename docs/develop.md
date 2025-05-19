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

We start implementing your agentic service with the folder package structure and the build of the _calculator_. To keep this example dead simple we create an agentic service which does nothing more than waiting. 

You can visit final implementation of this example service at [./apps/my_service/app.py](../apps/my_service/app.py) and [./apps/my_service/calc.py](../apps/my_service/calc.py)

### 1. setup project structure

Create a new directory `apps` to host the source code of your agentic service. Create a sub folder with the name of your service.

```bash
mkdir apps/my_service
touch apps/my_service/__init__.py
touch apps/my_service/calc.py
touch apps/my_service/app.py
```

> [!NOTE]
> In production each service represents a Ray _serve deployment_ with its own runtime environment including Python dependencies and environment settings.

### 3. implement entrypoint

We start development with the entrypoint. To keep this example as simple as possible we implement an entrypoint `execute` and a remote execution method `calculate` to simulate some heavy lifting. Implement both methods in `calc.py`.

```python
# ./apps/my_service/calc.py
import ray
from kodosumi.core import Tracer

async def execute(inputs: dict):
    tasks = inputs["tasks"]
    futures = [calculate.remote(i, tracer) for i in range(tasks)]
    pending = futures.copy()
    total = []
    while True:
        done, pending = ray.wait(pending, timeout=0.01)
        if done:
            ret = await asyncio.gather(*done)
            total.extend(ret)
            print(f"have result `{len(total)/tasks*100:.2f}%`")
        if not pending:
            break
        await asyncio.sleep(0)
    return total
```

We will extend this method later with some tracing and results trackimg. For now it is important to understand that this entrypoint utilises a method `calculate` to create futures which actually do the job. 

    futures = [calculate.remote(i, tracer) for i in range(tasks)]

The implementation of this method `calculate` which we ask Ray to distribute to it's workers is a _mock_:

```python
import time
from random import randint

@ray.remote
def calculate(i: int):
    # do your heavy-lifting work here
    time.sleep(randint(1, 3))
```

See the complete implementation of the _calculation_ in  [./apps/my_service/calc.py](../apps/my_service/calc.py).

### 4. setup app

We now proceed to setup the app with an endpoint to interact with your entrypoint. In `app.py`, set up the basic application structure:

```python
# ./apps/my_service/app.py
from kodosumi.core import ServeAPI

app = ServeAPI()
```

The `ServeAPI()` initialization creates a FastAPI application with kodosumi-specific extensions. It provides automatic OpenAPI documentation, error handling, authentication and access control, input validation, and configuration management.

The `app` instance will be used to define the service _endpoint_ with `@app.enter` and to define service meta data following OpenAPI standards.

### 4. define user interface

Before we implement the endpoint we will define the user interface of your service. We important _forms_ elements from `kodosumi.core`. See [forms overview](./forms.md) on the supported form and input elements.

```python
from kodosumi.core import forms

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
@app.enter(
    path="/",
    model=my_model,
    summary="My Service",
    description="Not so agentic example service.",
    tags=["Test"],
    version="1.0.0",
    author="your.email@domain.com",
)
async def enter(request: fastapi.Request, inputs: dict):
    inputs.get("tasks", None)
    if not tasks:
        error.add(tasks="Please specify the number of concurrent tasks.")
    if isinstance(tasks, str):
        tasks = int(tasks)
    MAX = 500
    if tasks > MAX:
        error.add(tasks=f"Maximum number of concurrent tasks is {MAX}")
    if error.has_errors():
        raise error
    return core.Launch(request, "apps.my_service.app:execute", inputs=inputs)
```

### 6. Deployment Configuration

Add the deployment configuration at the end of your `app.py`. 


```python
@serve.deployment
@serve.ingress(app)
class MyService: pass

fast_app = MyService.bind()
```

See [Configure Ray Serve Deployments](https://docs.ray.io/en/latest/serve/configure-serve-deployment.html) for additional options on your deployment. Be advised to gather some experience with Ray core components before you rollout your services. Understand [remote resource requirements](https://docs.ray.io/en/latest/ray-core/scheduling/resources.html#resource-requirements) and how to [limit concurrency to avoid OOM](https://docs.ray.io/en/latest/ray-core/patterns/limit-running-tasks.html#pattern-using-resources-to-limit-the-number-of-concurrently-running-tasks)

You can now run and deploy your service with `serve run apps.my_service.app:fast_app` and `serve deploy apps.my_service.app:fast_app`. You The deploys the service on port `8000`, see http://localhost:8000/, which retrieves the _inputs_ schema of your service.

Register your service with kodosumi, for example with

    koco serve --register http://localhost:8000/-/routes

And test the service at http://localhost:3370/inputs/-/localhost/8000/-/.

Use for example `curl` to POST a series of service requests:

    curl
    curl -b cookie -c cookie -X POST -d '{"name": "admin", "password": "admin"}' http://localhost:3370/login
    curl -X POST -H "Content-Type: application/json" -d '{"tasks": 10}' http://localhost:3370/inputs/-/localhost/8000/-/

```python
if __name__ == "__main__":
    sys.path.append(str(Path(__file__).parent.parent.parent))
    uvicorn.run("apps.my_service.app:app", 
                host="0.0.0.0", 
                port=8000, 
                reload=True)
```

## Best Practices

1. **Tracing**
   - Use `tracer.debug()` for logging debug information
   - Use `tracer.result()` for logging results
   - Implement proper error handling and logging

2. **Input Validation**
   - Always validate inputs in the `enter` method
   - Use `core.InputsError()` for structured error messages
   - Provide clear error messages to users

3. **Parallel Processing**
   - Use `@ray.remote` for parallel calculations
   - Consider task distribution and resource utilization
   - Implement proper error handling for distributed tasks

4. **Response Formatting**
   - Use `core.response.Markdown()` for formatted outputs
   - Structure responses clearly and consistently
   - Include relevant metadata in responses

5. **Testing**
   - Test your service locally before deployment
   - Verify input validation
   - Test parallel processing capabilities
   - Check error handling scenarios

## Example Implementation

For reference implementations, see:
- `apps/prime/app.py` for a simple calculation service
- `apps/form/app.py` for form handling examples
- `apps/throughput/app.py` for performance testing
- `apps/hymn/app.py` for AI integration examples

## Deployment

1. Ensure all dependencies are installed
2. Test the service locally
3. Deploy using the provided deployment configuration
4. Monitor the service using the built-in tracing capabilities

## Troubleshooting

Common issues and solutions:
1. Input validation errors - Check error messages in the `enter` method
2. Ray processing errors - Verify Ray configuration and resource availability
3. Deployment issues - Check port availability and service configuration
4. Performance issues - Monitor using tracer and adjust parallel processing parameters
