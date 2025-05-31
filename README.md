# kodosumi

kodosumi is the runtime environment to manage and execute agentic services at scale. The system is based on [Ray](https://ray.io) - a distributed computing framework - and a combination of [litestar](https://litestar.dev/) and [fastapi](https://fastapi.tiangolo.com/) to deliver agentic services to users or other agents. Similar to Ray, kodosumi follows a _Python first_ agenda.

kodosumi is one component of a larger eco system with [masumi and sokosumi](https://www.masumi.network/).

![Eco System](./docs/assets/ecosystem.png)


# introduction

kodosumi consists of three main building blocks. First, a _Ray cluster_ to execute agentic services at scale. kodosumi builds on top of Ray and actively manages the lifecycle and events of service executions from _starting_ to _finished_ or _error_. No matter you name your code an application, flow, service or script: The third building block is _your application_ which runs on top of kodosumi.

The following architecture shows the relation between the three building blocks: 1) your service on top of 2) kodosumi which operates 3) a distributed compute cluster with Ray secure and at scale.

[![kodosumi overview](./docs/assets/thumb/architecture.png)](./docs/assets/architecture.png)

You build and deploy your [Flow](./docs/concepts.md#flow) by providing an [endpoint](./docs/concepts.md#endpoint) (http route) and an [entrypoint](./docs/concepts.md#entrypoint) (Python callable) to kodosumi (left bottom blue box in the diagram). kodosumi delivers features for [access control](./docs/api.md#access-control), [flow control](./docs/api.md#flow-control) and manages [flow execution](./docs/api.md#execution-control) with Ray [head node](./docs/concepts.md#ray-head) and [worker nodes](./docs/concepts.md#ray-worker). [kodosumi spooler](./docs/concepts.md#spooler) gathers flow execution results and outputs into the [event stream](./docs/concepts.md#event-stream).

Deep-dive into [endpoints](./docs/concepts.md#endpoint) and how these translate into [entrypoints](./docs/concepts.md#entrypoint) of [flows](#flows) which operationalize the business logic of [agentic services](#agentic-service) or [agents](#agents) in the broader sense.

If you still need further background information read [why kodosumi](./docs/why.md)


# installation

The following quick guide

1. installs kodosumi and all prerequisites along the kodosumi example flows
2. starts Ray and kodosumi on your localhost
3. deploys example flows

This installation has been tested with versions `ray==2.46.0` and `python==3.12.6`.

If you want to skip the examples then continue with the [kodosumi development workflow](./docs/develop2.md) and start implementing your custom agentic service with the kodosumi framework.


### STEP 1 - clone and install `kodosumi-examples`.

Clone and install the `kodosumi-examples` into your Python Virtual Environment. The kodosumi and Ray packages are installed as a dependency to the examples.

```bash
git clone https://github.com/masumi-network/kodosumi-examples.git
cd ./kodosumi-examples
git checkout dev
pip install .
```

Since some of the examples utilize additional frameworks like `CrewAI` and `langchain` the installation of the kodosumi examples takes a while. All dependencies of the examples are installed. Please note that these dependencies are managed by Ray in production. See [deployment](./docs/deploy.md). 


### STEP 2 - prepare runtime environment

You need an OpenAI API key to run some of the examples. Specify the API key in `.env`.

    # ./env
    OPENAI_API_KEY=<-- enter-your-key -->


### STEP 3 - start ray as a daemon.

Start Ray _head_ node on your localhost:

```bash
ray start --head
```

Check `ray status` and visit ray dashboard at [http://localhost:8265](http://localhost:8265). For more information about ray visit [ray's documentation](https://docs.ray.io/en/latest).



### STEP 4 - deploy with uvicorn

You can launch each example service as a python module.

    python -m kodosumi.examples.hymn.app

This starts a uvicorn server at http://localhost:8011 which reports the OpenAPI specification at http://localhost:8011/openapi.json. Register this OpenAPI endpoint with

    koco start --register http://localhost:8011/openapi.json

### STEP 5 - look around

Visit kodosumi **[admin panel](http://localhost:3370)** at [http://localhost:3370](http://localhost:3370). The default user is defined in `config.py` and reads `name=admin` and `password=admin`. If one or more Ray serve applications are not yet available when kodosumi starts, you need to refresh the list of registered flows. Visit **[control screen](http://localhost:3370/admin/routes)** in the **[admin panel](http://localhost:3370/)** and click **RECONNECT**. Launch the **[Hymn Creator](http://localhost:3370/inputs/-/localhost/8001/hymn/-/)** from the **[service screen](http://localhost:3370/admin/flow)** and revisit results at the **[timeline screen](http://localhost:3370/timeline/view)**.

Visit [kodosumi panel overview](./docs/panel.md) to view some screenshots if you do not have the time and inclination. 

Stop the kodosumi services by hitting `CNTRL+C` in your terminal. The _spooler_ continues to run as a background daemon. You can stop the spooler with `koco spool --status`. Stop Ray _serve_ with `serve shutdown --yes` and Ray daemon with command `ray stop`.


### STEP 6 - run or deploy with Ray

Run the _Hymn Creator_ example with Ray serve:

    serve run kodosumi.examples.hymn.app:fast_app

Deploy the _Hymn Creator_ example with Ray serve:

    serve deploy kodosumi.examples.hymn.app:fast_app

Ray exposes all running deployments at http://localhost:8000/-/routes. You need to register this endpoint with _kodosumi panel_

    koco start --register http://localhost:8000/-/routes

If your panel is still up and running visit the [panel config screen](http://localhost:3370/admin/routes) and update the routes manually.

## Where to get from here?

* [admin panel introduction](./docs/panel.md)
* [API example](./docs/api.md)
* [development workflow](./docs/develop.md)
* [configuration and settings](./docs/config.md)