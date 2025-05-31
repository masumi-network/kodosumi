# kodosumi development workflow

This guide provides a step-by-step guide to implement, publish, deploy and run an agentic service using Ray and kodosumi.

## background information

We will implement an agent utilising [OpenAI](https://openai.com) to find news for a single or multiple companies 
operationalising the following LLM prompt:

    Identify news from {{start}} to {{end}} about company **"{{name}}"**. Format 
    the output as a bullet point list in the following format:

    * YYYY-mm-dd - [**Headline**](Link): Brief Summary of the news.
                        
    Only output the bullet point list about news in the specified date range. Do
    not include any other text or additional information. If you cannot find any 
    news for the given date range then output the text "no news found". 

## development workflow overview

The development process with kodosumi consists of two main work streams:

1. **Implementing the Entrypoint**

   The entrypoint serves as the foundation of your service, housing the core business logic. It acts as the central hub for distributed computing, where complex calculations or third party system requests are broken down and distributed across multiple processing units using Ray. This component is responsible for orchestrating parallel tasks and ensuring efficient resource utilization.

2. **Implementing the Endpoint**

   The endpoint establishes the HTTP interface for user interaction, providing a structured way to receive and process user input. It implements comprehensive input validation and manages the entire service lifecycle. This component is crucial for launching and monitoring the execution flow of your service.

Together, these components form a robust architecture that enables the creation of scalable and efficient agentic services. The entrypoint handles the computational complexity, while the endpoint ensures reliable user interaction and service management.

## step-by-step implementation guide

We start implementing the service with the folder package structure and the build of the _query_ function. 

### 1. create git remote

Create a public repository to host your agentic service. Ensure you have write access. For this example we use the following repository URL:

* https://github.com/plan-net/agentic-workflow-example.git

### 2. clone the repository

Clone the repository to your localhost:

```bash
    git clone https://github.com/plan-net/agentic-workflow-example.git
    cd agentic-workflow-example/
```

### 3. setup project structure

Create a new directory `./company_news` to host the source code of your agentic service. 

```bash
mkdir ./company_news
touch ./company_news/__init__.py
touch ./.gitignore
touch ./.env
```
Open `./.gitignore` and paste the following listing:

    __pycache__/
    data/*
    .env
    .venv

Open `./.env` and add your OpenAI api key:

    OPENAI_API_KEY=<add your key>

You need to have an [OpenAI API key](https://platform.openai.com/api-keys).

### 4. create Python Virtual Environment

Create and source a Python Virtual Environment with your system Python executable. The `.gitignore` above will ignore this `.venv` directory.

    python3 -m venv .venv
    source .venv/bin/activate

> [!NOTE]
> You need to locate the Python system executable. Depending on your operating system and setup this location differs.

Push our initial commit:

    git add .
    git commit -m "initial version"
    git push

### 5. install kodosumi

Install kodosumi from [PyPi](https://pypi.org/)

    pip install kodosumi

Or clone the latest `dev` trunk from [kodosumi at GitHub](https://github.com/masumi-network/kodosumi)

    git clone https://github.com/masumi-network/kodosumi
    cd kodosumi
    git checkout dev
    pip install .
    cd ..
    rm -Rf kodosumi

### 6. start ray

Start Ray on your localhost

    ray start --head

### 7. implement and test `query`

Implement the query function in `./company_news/query1.py`. 

```python
import datetime

from jinja2 import Template
from openai import OpenAI


def query(text: str, 
          start: datetime.datetime, 
          end: datetime.datetime) -> dict:
    template = Template("""
    Identify news from {{start}} to {{end}} about company **"{{name}}"**. 
    Format the output as a bullet point list in the following format:

    * YYYY-mm-dd - [**Headline**](Link): Brief Summary of the news.
                        
    Only output the bullet point list about news in the specified date range. 
    Do not include any other text or additional information. If you cannot find 
    any news for the given date range then output the text "no news found".    
    """)
    resp = chat(
        template.render(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            name=text)
    )
    return {**resp, **{
        "query": text,
        "start": start,
        "end": end
    }}
```

This method uses `jinja2` templating system to build a prompt with parameters `text`, `start`, and `end` and forwards the request to `chat` function.

```python
def chat(query: str, model="gpt-4o-mini"):
    t0 = datetime.datetime.now()
    client = OpenAI()
    response = client.responses.create(
        model=model,
        tools=[{"type": "web_search_preview"}],
        input=query
    )
    runtime = datetime.datetime.now() - t0
    return {
        "response": response.model_dump(),
        "output": response.output_text,
        "model": model,
        "runtime": runtime.total_seconds()
    }
```

At this stage and in `query1.py` we use `jinja2` which has been installed with kodosumi and `openai` which we have to install first:

    pip install openai

Test the `query` function with a Python interactive interpreter

```python
import datetime
from company_news.query1 import query

result = query(text="Serviceplan", 
                start=datetime.datetime(2025, 1, 1), 
                end=datetime.datetime(2025, 1, 31))
print(result["output"])    
```

### 8. distribute `query`

In this step you decorate `query` as a `@ray.remote` function and implement a driver function `batch` to process multiple requests with Ray.

```python
import ray

@ray.remote
def query(text: str, 
          start: datetime.datetime, 
          end: datetime.datetime) -> dict:
    ...
```

The driver function `batch`consumes a `List` of `str` and triggers a `chat` request with OpenAI for each refined query string.

```python
def batch(texts: List[str],
          start: datetime.datetime,
          end: datetime.datetime):

    refined = [t.strip() for t in texts if t.strip()]
    futures = [query.remote(t, start, end) for t in refined]
    remaining_futures = futures.copy()
    completed_jobs = 0
    results = []

    while remaining_futures:
        done_futures, remaining_futures = ray.wait(
            remaining_futures, num_returns=1)
        result = ray.get(done_futures[0])
        results.append(result)
        completed_jobs += 1
        p = completed_jobs / len(texts) * 100.
        print(f"{result['query']}\n{completed_jobs}/{len(texts)} = {p:.0f}%")
        if result["error"]:
            print(f"**Error:** {result['error']}")
        else:
            print(result["output"])

    return results
```

The `.remote()` statement forwards `batch` execution to Ray and creates futures to wait for.

    futures = [query.remote(t, start, end) for t in refined]

Test _batch processing_ with 

```python
import datetime
from company_news.query2 import batch
import ray

ray.init()
result = batch(texts=["Serviceplan", "Plan.Net", "Mediaplus"], 
               start=datetime.datetime(2018, 1, 1), 
               end=datetime.datetime(2018, 1, 31))
print(result) 
```

### 9. setup app

We now proceed to setup the app with an endpoint to interact with your entrypoint. For the simplicity of this example we add the endpoint implementation into `query3.py`.

In `query3.py`, set up the basic application structure:

```python
from kodosumi.core import ServeAPI
app = ServeAPI()
```

The `ServeAPI()` initialization creates a FastAPI application with kodosumi-specific extensions. It provides automatic OpenAPI documentation, error handling, authentication and access control, input validation, and some configuration management.

The `app` instance will be used to define the service _endpoint_ with `@app.enter` and to define service meta data following [OpenAPI specification](https://swagger.io/specification/#operation-object). We will do this in step **11** of this guide. Before we specify the inputs model.

### 10. define inputs model

Define the user interface of your service with the help of the _forms_ module. Import _forms_ elements from `kodosumi.core`. See [forms overview](./forms.md) on the supported form and input elements.

```python
from kodosumi.core import forms as F

news_model = F.Model(
    F.Markdown("""
    # Search News
    Specify the _query_ - for example the name of your client, the start and end date. You can specify multiple query. Type one query per line.
    """),
    F.Break(),
    F.InputArea(label="Query", name="texts"),
    F.InputDate(label="Start Date", name="start", required=True),
    F.InputDate(label="End Date", name="end", required=True),
    F.Submit("Submit"),
    F.Cancel("Cancel")
)
```

A simple form is rendered that displays a headline with some introductionary text, followed by a text area for the queries and a start and end date input field. 

### 11. implement endpoint

Implement the HTTP endpoint using the `@enter` decorator of the `ServeAPI` instance `app`. We will attach the input model defined in the previous step and declare key OpenAPI and extra properties (_summary_, _description_, and _tags_, _version_, _author_ for example).

On top of `ServeAPI` and `forms` we import `Launch` to start execution within the endpoint and `InputsError` for form validation and error handling.

```python
import fastapi
from kodosumi.core import InputsError
from kodosumi.core import Launch
```

Specify the endpoint function `enter` with

```python
@app.enter(
    path="/",
    model=news_model,
    summary="News Search",
    description="Search for news.",
    tags=["OpenAI"],
    version="1.0.0",
    author="m.rau@house-of-communication.com")
async def enter(request: fastapi.Request, inputs: dict):
    # parse and cleanse inputs
    query = inputs.get("texts", "").strip()
    start = datetime.datetime.strptime(inputs.get("start"), "%Y-%m-%d")
    end = datetime.datetime.strptime(inputs.get("end"), "%Y-%m-%d")
    texts = [s.strip() for s in query.split("\n") if s.strip()]
    # validate inputs
    error = InputsError()
    if not texts:
        error.add(texts="Please specify a query to search for news.")
    if start > end:
        error.add(start="Must be before or equal to end date.")
    if error.has_errors():
        raise error
    # launch execution
    return Launch(
        request, 
        "company_news.query3:run_batch", 
        inputs={"texts": texts, "start": start, "end": end}
    )
```

The method consists of three parts:

1. the `inputs` are parsed and cleansed
2. the `inputs` are validated
3. the execution is launched

The `Launch` object adresses function `run_batch` in `company_news.query3` which we implement later.

Finish Ray _serve_ setup and apply the Ray `@serve.deployment` and `@serve.ingress` decorators to create an _ingress deployment_. The `@serve.deployment` decorator is used to convert a Python class into a Deployment in Ray Serve. A deployment in Ray Serve is a group of actors that can handle traffic. It is defined as a single class with a number of options, including the number of “replicas” of the deployment, each of which will map to a Ray actor at runtime. Requests to a deployment are load balanced across its replicas.

The `@serve.ingress` decorator is used to wrap a deployment class with an application derived from `FastAPI` for HTTP request parsing.  It defines the HTTP handling logic for the application and can route to other deployments or call into them using the `DeploymentHandle` API.

```python
from ray import serve

@serve.deployment
@serve.ingress(app)
class NewsSearch: pass

fast_app = NewsSearch.bind()
```

The `fast_app` object is passed to Ray _serve_ for deployment. We will use the module/object _factory_ string `company_news.query3:fast_app` to configure and run deployments.

But before, we _wrap_ the entrypoint to `batch` into a function `run_batch` to convert `inputs` and pass the research request.

```python
async def run_batch(inputs: dict, tracer: Tracer):
    texts = inputs.get("texts", [])
    start = inputs.get("start", datetime.datetime.now())
    end = inputs.get("end", datetime.datetime.now())
    return await batch(texts, start, end, tracer)
```

Last but not least we refactor the `batch` function to be **async**. 

```python
```

If you carefully watch the function signature you recognise `inputs` and `tracer`. Both arguments are injected by the kodosumi `Launch` mechanic and carry the `inputs` arguments from the user and a `kodosumi.core.Tracer` object. Use this object to add markdown, text and other results to flow execution. The `tracer` will be passed to `batch`. A slightly modified `batch` function uses the `tracer` to create result markdown notes and _stdio_ output. Overall you will find the following events in the event stream of the _news search agent_:

* **`status`** - execution status transitions from _starting_ through _running_ to _finished_
* **`meta`** - service meta data with OpenAPI declarations among others
* **`inputs`** - inputs parameters
* **`result`** - intermediate results of the service as a data model, `dict` or `list` dump
* **`stdout`** - captured _prints_ and _writes_ to `stdout`
* **`final`** - the final response of the service as a data model, `dict` or `list` dump
* **`eof`** - end-of-stream message

See [Lifecycle and Events](./lifecycle.md#events) for further details.

```python
async def batch(texts: List[str], 
                start: datetime.datetime, 
                end: datetime.datetime,
                tracer: Optional[Tracer]=None):
    refined = [t.strip() for t in texts if t.strip()]
    futures = [query.remote(t, start, end) for t in refined]
    unready = futures.copy()
    completed_jobs = 0
    results = []
    while unready:
        ready, unready = ray.wait(unready, num_returns=1, timeout=1)
        if ready:
            result = ray.get(ready[0])
            results.append(result)
            completed_jobs += 1
            p = completed_jobs / len(texts) * 100.
            await tracer.markdown(f"#### {result['query']}")
            await tracer.markdown(f"{completed_jobs}/{len(texts)} = {p:.0f}%")
            if result["error"]:
                await tracer.markdown(f"**Error:** {result['error']}")
            else:
                await tracer.markdown(result["output"])
            await tracer.html("<div class='large-divider'></div>")
            print(f"Job completed ({completed_jobs}/{len(texts)})")
        await asyncio.sleep(1)
    return results
```















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

start uvicorn
start koco spool
start koco serve

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
