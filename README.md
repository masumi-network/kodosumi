# kodosumi

kodosumi is the runtime environment to manage and execute agentic services at scale. The system is based on [Ray](https://ray.io) - a distributed computing framework - and a combination of [litestar](https://litestar.dev/) and [fastapi](https://fastapi.tiangolo.com/) to deliver agentic services to users or other agents. Similar to Ray, kodosumi follows a _Python first_ agenda.

kodosumi is one component of a larger eco system with [masumi and sokosumi](https://www.masumi.network/).

![Eco System](./docs/assets/ecosystem.png)

# Introduction

kodosumi consists of three main building blocks. First, a _Ray cluster_ to execute agentic services at scale. kodosumi builds on top of Ray and actively manages the lifecycle and events of service executions from _starting_ to _finished_ or _error_. No matter you name your code an application, flow, service or script: The third building block is _your application_ which runs on top of kodosumi.

The following architecture shows the relation between the three building blocks: 1) your service on top of 2) kodosumi which operates 3) a distributed compute cluster with Ray secure and at scale.

[![kodosumi overview](./docs/assets/thumb/architecture.png)](./docs/assets/architecture.png)

You build and deploy your [Flow](./docs/concepts.md#flow) by providing an [endpoint](./docs/concepts.md#endpoint) (http route) and an [entrypoint](./docs/concepts.md#entrypoint) (Python callable) to kodosumi (left bottom blue box in the diagram). kodosumi delivers features for [access control](./docs/api.md#access-control), [flow control](./docs/api.md#flow-control) and manages [flow execution](./docs/api.md#execution-control) with Ray [head node](./docs/concepts.md#ray-head) and [worker nodes](./docs/concepts.md#ray-worker). [kodosumi spooler](./docs/concepts.md#spooler) gathers flow execution results and outputs into the [event stream](./docs/concepts.md#event-stream).

Deep-dive into [endpoints](./docs/concepts.md#endpoint) and how these translate into [entrypoints](./docs/concepts.md#entrypoint) of [flows](#flows) which operationalize the business logic of [agentic services](#agentic-service) or [agents](#agents) in the broader sense.


# installation

The following quick guide

1. installs kodosumi and all prerequisites
2. starts Ray and kodosumi on your localhost
3. deploys an example flow which ships with kodosumi

This installation has been tested with versions `ray==2.46.0` and `python==3.12.6`.

### STEP 1 - clone and install kodosumi.

```bash
pip install kodosumi
```

To install the latest `dev` from GitHub clone and install from source.

```bash
git clone https://github.com/masumi-network/kodosumi.git
cd kodosumi
pip install .
cd ..
```

### STEP 2 - create service home.

Create a directory `./home`. This directory will host agentic services. Each agentic service runs in a custom environment which matches the specific service requirements.

```bash
mkdir ./home
```
### STEP 3 - start ray as a daemon.

Start Ray with the `./home` directory as a package root so Ray can import from this directory which has been created in the previous step.

```bash
PYTHONPATH=./home ray start --head
```

Check `ray status` and visit ray dashboard at [http://localhost:8265](http://localhost:8265). For more information about ray visit [ray's documentation](https://docs.ray.io/en/latest).

### STEP 4 - prepare environment

To use [openai](https://openai.com/) or other API you might need to create a local file `.env` to define API keys. This follows the 12 factors to [store config in the environment](https://12factor.net/config).

Since the flow we are going to deploy uses [openai](https://openai.com/) you have to provide your API key in file `.env`.

```
OPENAI_API_KEY=...
```

### STEP 5 - deploy example app with `ray serve`

We will deploy kodosumi example apps. Clone kodosumi git source repository.


```bash
git clone https://github.com/masumi-network/kodosumi.git
```
Directory `./kodosumi/apps` contains various example services. Copy or link the cloned directory from `./kodosumi/apps` to `./home/apps`.

```bash
cp -r ./kodosumi/apps ./home/
```

Deploy example `apps.hymn.app` in folder `./apps`. Use Ray `serve deploy` to launch the service in your localhost Ray cluster.

```bash
serve deploy home/apps/hymn/config.yaml
```

Please be patient if the Ray serve application takes a moment to setup, install and deploy. Follow the deployment process with the Ray dashboard at [http://localhost:8265/#/serve](http://localhost:8265/#/serve). On my laptop initial deployment can easily take a couple of minutes.

### STEP 6 - start kodosumi

Finally start the kodosumi components and register the deployed ray endpoints available at 
[http://localhost:8001/-/routes](http://localhost:8001/-/routes). The port is defined in the `config.yaml` file. The path `/-/routes` reports available endpoints of active Ray deployments. 

```bash
koco start --register http://localhost:8001/-/routes
```

This command starts kodosumi spooler in the background and kodosumi panel and API in the foreground.

> [!NOTE]
> Command `koco start` starts the kodosumi spooler and the kodosumi panel API and is equivalent to:
> ```bash
> koco spool
> koco serve

### STEP 6 - Look around

Visit kodosumi admin panel at [http://localhost:3370](http://localhost:3370). The default user is defined in `config.py` and reads `name=admin` and `password=admin`. If one or more Ray serve applications are not yet available when kodosumi starts, you need to refresh the list of registered flows. Visit **Config Screen** at [(http://localhost:3370/admin/routes](http://localhost:3370/admin/routes) in the **Admin Panel** and click **Reconnect**. Run the _Hymn Creator_ a revisit results at [the timeline screen](http://localhost:3370/timeline/view),

Stop the kodosumi services by hitting `CNTRL+C` in your terminal. The _spooler_ continues to run as a background daemon. You can stop the spooler with `koco spool --status`. Stop Ray _serve_ with `serve shutdown --yes` and Ray daemon with command `ray stop`.

## Where to get from here?

* [admin panel introduction](./docs/panel.md)
* [panel API example](./docs/api.md)
* [development workflow](./docs/develop.md)

