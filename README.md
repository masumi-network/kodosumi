# kodosumi

For an overview of what kodosumi is and how it works, see [What is Kodosumi](./docs/what-is-kodosumi.md).

# installation

The following quick guide

1. installs kodosumi and all prerequisites along the kodosumi example flows
2. starts Ray and kodosumi on your localhost
3. deploys example flows

This installation has been tested with versions `ray==2.47.1` and `python==3.12.6`.

If you want to skip the examples then continue with the [kodosumi development workflow](./docs/develop.md) and start implementing your custom agentic service with the kodosumi framework.

## install and run examples

### STEP 1 - clone and install `kodosumi-examples`

Clone and install the `kodosumi-examples` into your Python Virtual Environment. The kodosumi and Ray packages are automatically installed as a dependency.

```bash
git clone https://github.com/masumi-network/kodosumi-examples.git
cd ./kodosumi-examples
pip install .
```

Since some of the examples utilize additional frameworks like `CrewAI` and `langchain` the installation of the kodosumi examples takes a while. All dependencies of the examples are installed. Please note that these dependencies are managed by Ray in production. See [deployment](./docs/deploy.md).

### STEP 2 - prepare runtime environment

You need an OpenAI API key to run some of the examples. Specify the API key in `.env`.

    # ./env
    OPENAI_API_KEY=<-- enter-your-key -->

### STEP 3 - start ray

Start Ray _head_ node on your localhost. Load environment variables with `dotenv` before starting ray:

```bash
dotenv run -- ray start --head
```

Check `ray status` and visit ray dashboard at [http://localhost:8265](http://localhost:8265). For more information about ray visit [ray's documentation](https://docs.ray.io/en/latest).

### STEP 4 - deploy

You have various options to deploy and run the example services. _kodosumi-examples_ repository ships with the following examples in `kodosumi_examples`:

- **hymn** - creates a hymn based on a given topic. The example demonstrates the use of [CrewAI](https://www.crewai.com/) and [OpenAI](https://openai.com/)
- **prime** - calculates prime number gaps. Distributes the tasks across the Ray cluster and demonstrates performance benefits.
- **throughput** - real-time experience of different event stream pressures with parameterized BPMs (beats per minute).
- **form** - demonstrates form elements supported by kodosumi.

You can run any of these examples. The next steps focus on `kodosumi_examples.hymn`.

#### alternative 1: run with uvicorn

You can launch each example service as a python module.

<<<<<<< Updated upstream
    uvicorn kodosumi_examples.hymn.app:app --port 8011
=======
    uvicorn kodosumi.examples.hymn.app:app --port 8011
>>>>>>> Stashed changes

This starts a uvicorn (Asynchronous Server Gateway Interface) server at http://localhost:8011. All HTTP endpoints of `app` are available at URL http://localhost:8011/openapi.json. Launch another terminal session, source the Python Virtual Environment and register this URL with kodosumi panel:

    koco start --register http://localhost:8011/openapi.json

Visit kodosumi **[admin panel](http://localhost:3370)** at [http://localhost:3370](http://localhost:3370). The default user is defined in `config.py` and reads `name=admin` and `password=admin`. Launch the **[Hymn Creator](http://localhost:3370/inputs/-/localhost/8011/-/)** from the **[service screen](http://localhost:3370/admin/flow)** and revisit results at the **[timeline screen](http://localhost:3370/timeline/view)**.

You can start another service `prime` in a new terminal with

<<<<<<< Updated upstream
    uvicorn kodosumi_examples.prime.app:app --port 8012
=======
    uvicorn kodosumi.examples.prime.app:app --port 8012
>>>>>>> Stashed changes

Register this service with [kodosumi panel config](http://localhost:3370/admin/routes) with both service endpoints

    http://localhost:8011/openapi.json
    http://localhost:8012/openapi.json

You can specify multiple _registers_ at `koco start`

    koco start --register http://localhost:8011/openapi.json --register http://localhost:8012/openapi.json

Running your services as standalone uvicorn applicaitons is best practice to facilitate debugging.


#### alternative 2: deploy and run with Ray serve

Run your services as Ray serve deployments. This is the preferred approach to deploy services in production. The downside of this approach is that you have to use remote debugging tools and attach to session breakpoints for debugging (see [Using the Ray Debugger](https://docs.ray.io/en/latest/ray-observability/user-guides/debug-apps/ray-debugging.html)).

Ray Serve is built on top of Ray, so it easily scales to many machines and offers flexible scheduling support such as fractional GPUs so you can share resources and serve many applications at low cost.

With Ray _serve_ you either run or deploy your services. Instead of the mechanics with uvicorn which refers the `app` application object, Ray serve demands the bound `fast_app` object. To test and improve your service run it with

<<<<<<< Updated upstream
    serve run kodosumi_examples.hymn.app:fast_app
=======
    serve run kodosumi.examples.hymn.app:fast_app
>>>>>>> Stashed changes

In contrast to the previous command a `serve deploy` command is used to deploy your Serve application to the Ray cluster. It sends a deploy request to the cluster and the application is deployed asynchronously. This command is typically used for deploying applications in a production environment.

    serve deploy kodosumi.examples.hymn.app:fast_app

Using Ray _serve run_ or _deploy_ the `--register` must connect to Ray's proxy URL `/-/routes`. With `serve run` or `deploy` the port defaults to `8000` and you start `koco start` with the Ray serve endpoint http://localhost:8000/-/routes.

    koco start --register http://localhost:8000/-/routes

<<<<<<< Updated upstream

#### alternative 3: multi-service setup with Serve config files
=======
#### multi-service setup with Serve config files
>>>>>>> Stashed changes

`serve run` and `serve deploy` feature single services. Running multiple uvicorn services is possible but soon gets dity and quirky. For multi-service deployments use Ray serve _config files_.

In directory `./data/config` create a file `config.yaml` with _serve's_ overarching configuration, for example

```yaml
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
```

Alongside this file `config.yaml` create service configuration files. For each service deployment create a dedicated configuration file:

##### `hymn.yaml`

```yaml
name: hymn
route_prefix: /hymn
<<<<<<< Updated upstream
import_path: kodosumi_examples.hymn.app:fast_app
runtime_env: 
=======
import_path: kodosumi.examples.hymn.app:fast_app
runtime_env:
>>>>>>> Stashed changes
  pip:
    - crewai
    - crewai_tools
  env_vars:
    OTEL_SDK_DISABLED: "true"
    OPENAI_API_KEY: <-- enter-you-API-key -->
```

##### `prime.yaml`

```yaml
name: prime
route_prefix: /prime
import_path: kodosumi_examples.prime.app:fast_app
```

##### `throughput.yaml`

```yaml
name: throughput
route_prefix: /throughput
<<<<<<< Updated upstream
import_path: kodosumi_examples.throughput.app:fast_app
runtime_env: 
=======
import_path: agentic.examples.throughput.app:fast_app
runtime_env:
>>>>>>> Stashed changes
  pip:
    - lorem-text
```

Test this deployment set with

    koco deploy --dry-run --file ./data/config/config.yaml

With success, stop `ray serve` and perform a Ray serve deployment

    serve shutdown --yes  # this is equivalent to koco deploy --stop
    koco deploy --run --file ./data/config/config.yaml

Restart `koco start` with the Ray serve endpoint http://localhost:8001/-/routes as configured in `config.yaml`.

    koco start --register http://localhost:8001/-/routes

If one or more Ray serve applications are not yet available when kodosumi starts, you need to refresh the list of registered flows. Visit **[control screen](http://localhost:3370/admin/routes)** in the **[admin panel](http://localhost:3370/)** and click **RECONNECT**.

Adding and removing deployments is operationalized with config files in `./data/config`. All files alongside `config.yaml` are deployed. You can test your deployment setup with `koco deploy --dry-run --file ./data/config/config.yaml`.

**Where to get from here?**

<<<<<<< Updated upstream
* continue with [kodosumi development workflow](./docs/develop.md) 
* see the admin panel [screenshots](./docs/panel.md)
* read about [basic concepts and terminology](./concepts.md)
=======
- continue with [kodosumi development workflow](./docs/develop.md)
- see the admin panel [screenshots](./panel.md)
- read about [basic concepts and terminology](./concepts.md)
>>>>>>> Stashed changes
