# deployment guide

You can reproduce this deployment guide on localhost. Nevertheless this guide is a blueprint for production, too. All environment variables, package sources, dependencies and other settings are declared with configuration _YAML_ files. This is the preferred approach to production settings.

As a consequence, this guide is a lot about managing YAML files. kodosumi simplifies the management of these configuration files by splitting the configuration into a _base_ and multiple _app_ configurations. 

This deployment is based on the `company_news` service built in [development workflow](./develop.md).

Let's start with creating a root directory, i.e.

    mkdir ~/kodosumi
    cd kodosumi
    python -m venv .venv
    source .venv/bin/activate

    pip install kodosumi

If instead you want to install the latest development trunk run

    pip install "kodosumi @ git+https://github.com/masumi-network/kodosumi.git@dev" 

Start your Ray cluster with

    ray start --head

In the previous examples you did run `koco start` which launches the kodosumi _spooler_ daemon *plus* the kodosumi admin _panel_ web app and API. In this example we start the spooler and the panel seperately. Start with the spooler

    koco spool

This starts the spooler in the background and as a daemon. You can review daemon status with

    koco spool --status

Stop the spooler later with `koco spool --stop`.

The spooler automatically creates directory `./data/config` to host Ray _serve_ configuration files as specified with configuration parameter `YAML_BASE`. The _yaml_ base defaults to `./data/config/config.yaml` and locates relative to the directory where you start `koco spool` and `koco serve`.

Create file `./data/config/config.yaml` with Ray _serve_ base configuraiton. The following yaml configuration is a good starting point. For further details read Ray's documentation about [Serve Config Files](https://docs.ray.io/en/latest/serve/production-guide/config.html#serve-in-production-config-file).

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

We will deploy the _agentic-workflow-example_ service **`company_news`**.

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
