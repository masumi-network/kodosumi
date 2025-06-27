This guide provides a step-by-step guide to implement, publish, deploy and run your custom agentic service using Ray and Kodosumi.


## Background information

We will implement an agent utilising [OpenAI](https://openai.com) to find news for a single or multiple companies operationalising the following LLM prompt:

> Identify news from `{{start}}` to `{{end}}` about company **"`{{name}}`"**. Format 
> the output as a bullet point list in the following format:
>
> `* YYYY-mm-dd - [**Headline**](Link): Brief Summary of the news.`
>                     
> Only output the bullet point list about news in the specified date range. Do
> not include any other text or additional information. If you cannot find any 
> news for the given date range then output the text _"no news found"_. 


## Development workflow overview

The development process with Kodosumi consists of two main work streams:

1. **Implementing the Entrypoint**

   The entrypoint serves as the foundation of your service, housing the core business logic. It acts as the central hub for distributed computing, where complex calculations or third party system requests are broken down and distributed across multiple processing units using Ray. This component is responsible for orchestrating parallel tasks and ensuring efficient resource utilization.

2. **Implementing the Endpoint**

   The endpoint establishes the HTTP interface for user interaction, providing a structured way to receive and process user input. It implements comprehensive input validation and manages the entire service lifecycle. This component is crucial for launching and monitoring the execution flow of your service.

Together, these components form a robust architecture that enables the creation of scalable and efficient agentic services. The entrypoint handles the computational complexity, while the endpoint ensures reliable user interaction and service management.


## Step-by-step implementation guide

We start implementing the service with the folder package structure and the build of the _query_ function. 


### 1. Create git remote

Create a public repository to host your agentic service. Ensure you have write access. For this example we use the following repository URL:

* https://github.com/plan-net/agentic-workflow-example.git


### 2. Create Python Virtual Environment

Create and source a Python Virtual Environment with your system Python executable. 

    python3 -m venv .venv
    source .venv/bin/activate

> [!NOTE]
> You need to locate the Python system executable. Depending on your operating system and setup this location differs.


### 3. Clone the repository

Clone the repository to your localhost:

```bash
    git clone https://github.com/plan-net/agentic-workflow-example.git
    cd agentic-workflow-example/
```


### 4. Setup project structure

Create a new directory `./company_news` inside your local working directory `./agentic-workflow-example` to host your package. 

```bash
mkdir ./company_news
```

> [!NOTE]
> Use the _flat package layout_. The _flat layout_ simplifies the deployment process as we will see later in [deployment](./deploy). In contrast to the _src layout_ the the _flat layout_ does **not** need any additional installation step to be _importable_ by Python. 

Add the basic package structure and create the usual suspects along with `query.py` to deliver the agentic service.

```bash
touch ./company_news/__init__.py
touch ./.gitignore
touch ./.env
```

Open `./.gitignore` and paste the following listing:

```text
__pycache__/
data/*
.env
.venv
```

Open `./.env` and add your OpenAI api key:

```text
OPENAI_API_KEY=<add your key>
```

You need to have an [OpenAI API key](https://platform.openai.com/api-keys) to run this example.

Push our initial commit:

    git add .
    git commit -m "initial version"
    git push


### 5. Install Kodosumi

Install Kodosumi from [PyPi](https://pypi.org/)

    pip install kodosumi

Or clone the latest `dev` trunk from [Kodosumi at GitHub](https://github.com/masumi-network/kodosumi)

    git clone https://github.com/masumi-network/kodosumi
    cd kodosumi
    git checkout dev
    pip install .
    cd ..
    rm -Rf kodosumi


### 6. Start ray

Start Ray on your localhost. Load `.env` into the environment variables before

    dotenv run -- ray start --head


### 7. Implement and test `query`

Implement the query function in `./company_news/query.py`. 

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
    try:
        resp = chat(
            template.render(
                start=start.strftime("%Y-%m-%d"),
                end=end.strftime("%Y-%m-%d"),
                name=text)
        )
        return {**resp, **{
            "query": text,
            "start": start,
            "end": end,
            "error": None,
        }}
    except Exception as e:
        return {
            "query": text,
            "start": start,
            "end": end,
            "error": e
        }
```

This method uses `jinja2` templating system to build a prompt with parameters `text`, `start`, and `end` and forwards the request to `chat` function.

Add the `chat` function at the end of `query.py`.

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

At this stage and in `query.py` we use `jinja2` which has been installed with kodosumi and `openai` which needs to be installed first:

    pip install openai


Test the `query` function with a Python interactive interpreter

```python
import datetime
from company_news.query import query
from dotenv import load_dotenv

load_dotenv()
result = query(text="Serviceplan", 
                start=datetime.datetime(2024, 1, 1), 
                end=datetime.datetime(2024, 12, 31))
print(result["output"])    
```


### 8. Distribute `query`

In this next step you decorate `query` as a `@ray.remote` function and implement a driver function `batch` to process multiple concurrent queries with Ray.

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
from typing import List

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
from dotenv import load_dotenv
import ray
from company_news.query import batch

load_dotenv()
ray.init()
result = batch(texts=["Serviceplan", "Plan.Net", "Mediaplus"], 
               start=datetime.datetime(2018, 1, 1), 
               end=datetime.datetime(2018, 1, 31))
print(result) 
```


### 9. Setup app

We now proceed to setup the app with an endpoint to interact with your service. For the simplicity of this example we add the endpoint implementation directly into `query.py`.

At the end of `query.py`, set up the basic application structure:

```python
from kodosumi.core import ServeAPI
app = ServeAPI()
```

The `ServeAPI()` initialization creates a FastAPI application with Kodosumi-specific extensions. It provides automatic OpenAPI documentation, error handling, authentication and access control, input validation, and some configuration management.

The `app` instance will be used to define the service _endpoint_ with `@app.enter` and to define service meta data following [OpenAPI specification](https://swagger.io/specification/#operation-object). We will do this in step **11** of this guide. Before we specify the inputs model.


### 10. Define inputs model

Define the user interface of your service with the help of the _forms_ module. Import _forms_ elements from `kodosumi.core`. See [forms overview](./forms.md) on the supported form input elements.

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


### 11. Implement endpoint

Implement the HTTP endpoint using the `@enter` decorator of the `ServeAPI` instance `app`. We will attach the input model defined in the previous step and declare key OpenAPI and extra properties (_summary_, _description_, and _tags_, _version_, _author_ for example).

On top of `ServeAPI` and `forms` we import `Launch` to start execution within the endpoint and `InputsError` for form validation and error handling. Kodosumi `Tracer` will be used to log results and debug message. We import `asyncio` and some `typing` which we will need later.

```python
import fastapi
from kodosumi.core import InputsError
from kodosumi.core import Launch
from kodosumi.core import Tracer
import asyncio
from typing import Optional, List
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
        "company_news.query:run_batch", 
        inputs={"texts": texts, "start": start, "end": end}
    )
```

The method consists of three parts:

1. the `inputs` are parsed and cleansed
2. the `inputs` are validated
3. the execution is launched

The `Launch` object adresses function `run_batch` in `company_news.query` which we implement later.


### 12. Create ingress deployment

Finish Ray _serve_ setup and apply the Ray `@serve.deployment` and `@serve.ingress` decorators to create an _ingress deployment_. The `@serve.deployment` decorator is used to convert a Python class into a Deployment in Ray Serve. A deployment in Ray Serve is a group of actors that can handle traffic. It is defined as a single class with a number of options, including the number of “replicas” of the deployment, each of which will map to a Ray actor at runtime. Requests to a deployment are load balanced across its replicas.

The `@serve.ingress` decorator is used to wrap a deployment class with an application derived from `FastAPI` for HTTP request parsing.  It defines the HTTP handling logic for the application and can route to other deployments or call into them using the `DeploymentHandle` API.

```python
from ray import serve

@serve.deployment
@serve.ingress(app)
class NewsSearch: pass

fast_app = NewsSearch.bind()
```

The `fast_app` object is passed to Ray _serve_ for deployment. We will use the module/object _factory_ string `company_news.query:fast_app` to configure and run deployments.


### 13. Refactor `batch`

But before, we _wrap_ the entrypoint to `batch` into a function `run_batch` to convert `inputs` and pass the research request.

```python
async def run_batch(inputs: dict, tracer: Tracer):
    texts = inputs.get("texts", [])
    start = inputs.get("start", datetime.datetime.now())
    end = inputs.get("end", datetime.datetime.now())
    return await batch(texts, start, end, tracer)
```

Last but not least we refactor the `batch` function to be **async**.  Add an updated version of `batch` at the end of `query.py` and remove the "old" function `batch` further up in file `query.py`.

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

If you carefully watch the function signature you recognise `inputs` and `tracer`. Both arguments are injected by the Kodosumi `Launch` mechanic and carry the `inputs` arguments from the user and a `kodosumi.core.Tracer` object. Use this object to add markdown, text and other results to flow execution. The `tracer` will be passed to `batch`. A slightly modified `batch` function uses the `tracer` to create result markdown notes and _stdio_ output. 


### 14. Test with uvicorn

We test the `app` in `query.py` with uvicorn

    uvicorn company_news.query:app --port 8013

Access the exposed endpoint at http://localhost:8013/ and you will retrieve the `inputs` scheme defined above with named form elements _texts_, _start_, and _end_.

This time we will skip registering the OpenAPI endpoint http://localhost:8013/openapi.json with `koco start`. Instead we immediately turn to Ray _serve_ deployments.


### 15. Test with Ray _serve_

Run the deployment, this time with the Ray `fast_app` object. Ensure you Ray cluster is up and running, i.e. with `ray status`.

    serve run company_news.query:fast_app

Ray reports available routes at http://localhost:8000/-/routes. Verify the routes are properly published at http://localhost:8000/-/routes and retrieve the schema this time at http://localhost:8000/.

Again we will skip `koco serve` until we have a proper deployment.


### 16. Deploy with Ray _serve_

Deploy with Ray _serve_ and run `koco start` to register your service with Kodosumi.

    serve deploy company_news.query:fast_app
    koco start --register http://localhost:8000/-/routes

Now launch the admin panel at http://localhost:3370 and run a test from http://localhost:3370/inputs/-/localhost/8000/-/. Retrieve the inputs scheme from [/-/localhost/8000/-/](http://localhost:3370/-/localhost/8000/-/), and test the service at [/inputs/-/localhost/8000/-/](http://localhost:3370/inputs/-/localhost/8000/-/).

Revisit the raw event stream with a given _Flow Identifier_ (`fid`) at http://localhost:3370/outputs/stream/{fid}. Overall you will find the following events in the event stream of the _news search agent_:

* **`status`** - execution status transitions from _starting_ through _running_ to _finished_
* **`meta`** - service meta data with OpenAPI declarations among others
* **`inputs`** - inputs parameters
* **`result`** - intermediate results of the service as a data model, `dict` or `list` dump
* **`stdout`** - captured _prints_ and _writes_ to `stdout`
* **`final`** - the final response of the service as a data model, `dict` or `list` dump
* **`eof`** - end-of-stream message

See [Lifecycle and Events](./lifecycle.md#events) for further details.

Use for example `curl` to POST a service requests with the panel API after successful authentication:

    curl -b cookie -c cookie -X POST -d '{"name": "admin", "password": "admin"}' http://localhost:3370/api/login
    curl -b cookie -c cookie -X POST -d '{"texts": "audi\nbmw", "start": "2025-01-01", "end": "2025-01-31"}' http://localhost:3370/-/localhost/8000/-/

The response returns the _Flow Identifier_ (`fid`).

```json
{
    "result": "...",
    "elements": ["..."]
}
```

**Where to get from here?**

* continue with [Kodosumi deployment workflow](./deploy.md)
