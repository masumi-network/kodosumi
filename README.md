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

The development notes provide an overview for various flavours on how to run and deploy agentic services.

Follow the examples:

* [Function Blueprint - ](apps/example7.py)
* [Search for Armstrong Numbers](apps/example1.py)
* [Search for Armstrong Numbers with nested remote calls](apps/example2.py)
* [Crew of Agents to draft a Hymn using OpenAI - ](apps/example3.py)
* [Crew of Agents to draft a Marketing Campaign using OpenAI - ](apps/example4/service.py)
* [Crew of Agents to draft a Job Posting using OpenAI - ](apps/example4/service.py)
