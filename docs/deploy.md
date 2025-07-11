# deployment guide

## runtime environment setup

You can reproduce this deployment guide on localhost. Nevertheless this guide is a blueprint for production. All environment variables, package sources, dependencies and other settings are declared with configuration _YAML_ files. This is the preferred approach to production settings with Ray. See [Ray Production Guide](https://docs.ray.io/en/latest/serve/production-guide/index.html).

As a consequence, this guide is a lot about managing YAML files. kodosumi simplifies the management of these configuration files by splitting the configuration into a _base_ and multiple _app_ configurations. 

This deployment is based on the `company_news` service built in [development workflow](./develop.md).

Let's start with creating a root directory to host our runtime environment, i.e.

    mkdir ~/kodosumi
    cd kodosumi

Create a Python Virtual Environment with

    python -m venv .venv  # depends on OS setup
    source .venv/bin/activate

> [!NOTE]
> The location of your system's Python executable `python` might vary. 

Next, install kodosumi from the [Python package index](https://pypi.org/)

    pip install kodosumi

if instead you prefer to install the latest development trunk run

    pip install "kodosumi @ git+https://github.com/masumi-network/kodosumi.git@dev" 

Start your Ray cluster with

    ray start --head

In the previous examples you did run `koco start` which launches the kodosumi _spooler_ daemon ***PLUS*** the kodosumi admin _panel_ web app and API. In the current example we start the spooler and the panel seperately. We start with the spooler

    koco spool

This starts the spooler in the background and as a daemon. You can review daemon status with

    koco spool --status

Stop the spooler later with `koco spool --stop`.

The spooler automatically creates directory `./data/config` to host Ray _serve_ configuration files as specified with configuration parameter `YAML_BASE`. The _yaml_ base defaults to `./data/config/config.yaml` and locates the base _relative_ to the directory where you start `koco spool` and `koco serve`.

Create file `./data/config/config.yaml` with Ray _serve_ base configuration. The following yaml configuration is a good starting point. For further details read Ray's documentation about [Serve Config Files](https://docs.ray.io/en/latest/serve/production-guide/config.html#serve-in-production-config-file).

```yaml
# ./data/config/config.yaml
proxy_location: EveryNode
http_options:
  host: 0.0.0.0
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

We will deploy the _agentic-workflow-example_ service as a package **`company_news`**.

Create the first app configuration named `company_news.yaml` with the following content:

```yaml
# ./data/config/company_news.yaml
name: company_news
route_prefix: /company_news
import_path: company_news.query:fast_app
runtime_env: 
  py_modules:
  - https://github.com/plan-net/agentic-workflow-example/archive/45aabddf234cf8beb7118b400e7cb567776e458a.zip
  pip:
  - openai
  env_vars:
    OTEL_SDK_DISABLED: "true"
    OPENAI_API_KEY: <-- your-api-key -->
```

Deploy your configuration set with

    koco deploy ./data/config/config.yaml

This will apply your _base_ configuration from `./data/config/config.yaml` and adds a key `application` with records from `./data/config/company_news.yaml`.

With running Ray, spooler and app we now start the kodosumi panel and register Ray deployments

    koco serve --register http://localhost:8001/-/routes

See [Configure Ray Serve Deployments](https://docs.ray.io/en/latest/serve/configure-serve-deployment.html) for additional options on your deployment. Be advised to gather some experience with Ray core components before you rollout your services. Understand [remote resource requirements](https://docs.ray.io/en/latest/ray-core/scheduling/resources.html#resource-requirements) and how to [limit concurrency to avoid OOM issues](https://docs.ray.io/en/latest/ray-core/patterns/limit-running-tasks.html#pattern-using-resources-to-limit-the-number-of-concurrently-running-tasks)


## deployment API

Use _kodosumi panel API_ to change your Ray _serve_ deployments at runtime. The panel API ships with a simple CRUD interfacce to create, read, update and delete deployment configurations including the _base configuration_ with `config.yaml`.

The following Python snippets demonstrates API usage.

```python
import httpx

# login
resp = httpx.get("http://localhost:3370/login?name=admin&password=admin")
cookies = resp.cookies

# retrieve Ray serve deployments status
resp = httpx.get("http://localhost:3370/deploy", cookies=cookies)
resp.json()
```

With services `company_news` and all kodosumi-examples up and running the request for example yields:

```json
{
    "prime": "running",
    "throughput": "running",
    "hymn": "running",
    "form": "running",
    "company_news": "running"
}
 ```

Now we stop and remove service `prime` from active Ray _serve_ deployments with

```python
# remove deployment "prime"
resp = httpx.delete("http://localhost:3370/deploy/prime", cookies=cookies)
```

After this `DELETE` request the status yields

```json
{
    "prime": "to-stop",
    "throughput": "running",
    "hymn": "running",
    "form": "running",
    "company_news": "running"
}
 ```

To request Ray _serve_ to enter this state `POST /serve` with

