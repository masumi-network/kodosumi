import pytest

from kodosumi.dtypes import RegisterFlow
from ray import serve
import asyncio
from multiprocessing import Process
import httpx

from fastapi import Request, Response
from pydantic import BaseModel
from kodosumi.core import ServeAPI, Launch
from kodosumi.service.inputs.forms import Model, InputText, Checkbox, Submit, Cancel
from tests.test_role import auth_client
from tests.test_execution import run_uvicorn

app = ServeAPI()

form_model = Model(
    InputText(label="Name", name="name", placeholder="Enter your name"),
    Checkbox(label="Active", name="active", option="ACTIVE", value=False),
    Submit("Submit"),
    Cancel("Cancel"),
)

class FormData(BaseModel):
    name: str
    active: bool = False

@app.enter(
    "/",
    model=form_model,
    summary="Model Example",
    tags=["flow"],
)
async def post(inputs: dict, request: Request) -> Response:
    """Echo-Endpunkt, der die Eingaben zurückliefert."""
    return Launch(request, "tests.test_execution:runner", inputs=inputs)

def create_app():
    app = ServeAPI()
    form_model = Model(
        InputText(label="Name", name="name", placeholder="Enter your name"),
        Checkbox(label="Active", name="active", option="ACTIVE", value=False),
        Submit("Submit"),
        Cancel("Cancel"),
    )
    class FormData(BaseModel):
        name: str
        active: bool = False

    @app.enter(
        "/",
        model=form_model,
        summary="Factory Example",
        tags=["flow"],
        organization="Factory Organization",
        author="Factory Author",    
        deprecated=False,
        description="Factory Description",
    )
    async def post(data: FormData, request: Request) -> dict:
        """Echo-Endpunkt, der die Eingaben zurückliefert."""
        # todo: test this
        return {"result": data.model_dump()}
        return Response(content="hi")   

    @app.get(
        "/get",
        entry=True,
        summary="Get Example",
        tags=[],
        organization="Get Organization",
        author="Get Author",    
        deprecated=True,
        description="Get Description",
    )
    async def post(request: Request) -> dict:
        return {"result": None}

    return app


@pytest.fixture
def fake_openapi(monkeypatch):
    """Patch ``kodosumi.service.endpoint._get_openapi`` so that no external
    HTTP requests are executed during the test run.

    The patched coroutine always returns a minimal OpenAPI specification that
    is sufficient for the ``kodosumi.service.endpoint.register`` helper to
    create one dummy :class:`~kodosumi.dtypes.EndpointResponse` entry.  This
    keeps the tests completely offline and deterministic.
    """
    stub_spec = {
        "paths": {
            "/predict": {
                "get": {
                    "summary": "Run prediction",
                    "description": "Dummy endpoint used for unit-testing",
                    "tags": ["ml"],
                    "x-kodosumi": True,
                }
            }
        }
    }

    async def _fake_get_openapi(_: str):  # noqa: D401 – simple stub
        return stub_spec

    monkeypatch.setattr(
        "kodosumi.service.endpoint._get_openapi", _fake_get_openapi
    )
    return stub_spec

@pytest.mark.asyncio
async def test_flow_list_empty(auth_client):
    """Verify that :http:get:`/flow` returns an empty list when no flows are
    registered."""
    response = await auth_client.get("/flow")
    assert response.status_code == 200
    print("x"*80)
    payload = response.json()
    assert payload["items"] == []
    assert payload["offset"] is None
    print("x"*80)


@pytest.mark.asyncio
async def test_flow_register(fake_openapi, auth_client):
    """Register a new Flow source via :http:post:`/flow/register` and ensure
    that the returned payload contains exactly the Endpoint description
    extracted from the mocked OpenAPI specification.
    """
    register_payload = RegisterFlow(
        url="http://dummy/openapi.json").model_dump()
    response = await auth_client.post("/flow/register", json=register_payload)
    assert response.status_code == 201

    endpoints = response.json()
    assert isinstance(endpoints, list)
    assert len(endpoints) == 1
    ep = endpoints[0]
    assert ep["method"] == "GET"
    assert ep["summary"] == "Run prediction"

    # The Flow should now be listed by GET /flow
    response = await auth_client.get("/flow")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["items"]) == 1

    # And its tag should be visible under /flow/tags
    response = await auth_client.get("/flow/tags")
    assert response.status_code == 200
    tags = response.json()
    assert tags == {"ml": 1}


@pytest.mark.asyncio
async def test_flow_unregister(fake_openapi, auth_client):
    """Ensure that :http:post:`/flow/unregister` removes a previously
    registered Flow source and that subsequent listing endpoints reflect the
    removal."""
    url = "http://dummy/openapi.json"

    # First register
    response = await auth_client.post("/flow/register", json={"url": url})
    assert response.status_code == 201

    # Then unregister
    response = await auth_client.post("/flow/unregister", json={"url": url})
    assert response.status_code == 200
    assert response.json() == {"deletes": [url]}

    # Verify that the Flow list is empty again
    response = await auth_client.get("/flow")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []

    # Then unregister
    response = await auth_client.post("/flow/unregister", json={"url": url})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_flow_update(fake_openapi, auth_client):
    """Call :http:put:`/flow/register` which forces a refresh of all
    registered Flow sources.  The endpoint should return meta-information about
    the update process and leave the registered endpoints unchanged."""
    url = "http://dummy/openapi.json"
    await auth_client.post("/flow/register", json={"url": url})
    response = await auth_client.get("/flow")
    assert response.status_code == 200

    response = await auth_client.put("/flow/register")
    assert response.status_code == 200
    payload = response.json()
    for key in ("summaries", "urls", "deletes", "sources", "connected"):
        assert key in payload
    response = await auth_client.get("/flow")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1 


@pytest.mark.asyncio
async def test_flow_register_real(auth_client):
    port = 8123
    proc = Process(target=run_uvicorn, args=("tests.test_flow:app", port,))
    proc.start()
    import httpx
    url = f"http://localhost:{port}/openapi.json"
    async with httpx.AsyncClient() as client:
        for _ in range(40):  # max ~10 s
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        else:
            pytest.fail("uvicorn-App konnte nicht gestartet werden")

    # Registriere den Flow
    resp = await auth_client.post("/flow/register", json={"url": url})
    assert resp.status_code == 201
    endpoints = resp.json()
    assert [ep["summary"] for ep in endpoints] == ["Model Example"]
    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == len(endpoints)
    assert items[0]["summary"] == "Model Example"
    assert items[0]["tags"] == ["flow"]
    assert items[0]["method"] == "GET"
    assert items[0]["url"] == "/-/localhost/8123/-/"
    assert items[0]["source"] == 'http://localhost:8123/openapi.json'
    assert items[0]["description"] is None
    assert items[0]["deprecated"] is False
    assert items[0]["author"] is None
    assert items[0]["organization"] is None
    proc.terminate()
    proc.join() 


@pytest.mark.asyncio
async def test_flow_register_real_factory(auth_client):
    port = 8124
    proc = Process(target=run_uvicorn, args=("tests.test_flow:create_app", port,))
    proc.start()
    import httpx
    url = f"http://localhost:{port}/openapi.json"
    async with httpx.AsyncClient() as client:
        for _ in range(40):  # max ~10 s
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        else:
            pytest.fail("uvicorn-App konnte nicht gestartet werden")

    resp = await auth_client.post("/flow/register", json={"url": url})
    assert resp.status_code == 201
    endpoints = resp.json()
    assert [ep["summary"] for ep in endpoints] == ["Factory Example", "Get Example"]
    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == len(endpoints)
    assert items[0]["summary"] == "Factory Example"
    assert items[0]["tags"] == ["flow"]
    assert items[0]["method"] == "GET"
    assert items[0]["url"] == "/-/localhost/8124/-/"
    assert items[0]["source"] == 'http://localhost:8124/openapi.json'
    assert items[0]["description"] == "Factory Description"
    assert items[0]["deprecated"] is False
    assert items[0]["author"] == "Factory Author"
    assert items[0]["organization"] == "Factory Organization"
    assert items[1]["summary"] == "Get Example"
    assert items[1]["tags"] == []
    assert items[1]["method"] == "GET"
    assert items[1]["url"] == "/-/localhost/8124/-/get"
    assert items[1]["source"] == 'http://localhost:8124/openapi.json'
    assert items[1]["description"] == "Get Description"
    assert items[1]["deprecated"] is True
    proc.terminate()
    proc.join() 

async def wait_for(url: str):
    async with httpx.AsyncClient() as client:
        for _ in range(40):  # max ~10 s
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError(f"failed to connect to {url}")

@pytest.mark.asyncio
async def test_flow_register_two(auth_client):
    proc1 = Process(target=run_uvicorn, args=("tests.test_flow:create_app", 8125,))
    proc1.start()
    proc2 = Process(target=run_uvicorn, args=("tests.test_flow:app", 8126,))
    proc2.start()
    url1 = f"http://localhost:8125/openapi.json"
    url2 = f"http://localhost:8126/openapi.json"
    await wait_for(url1)
    await wait_for(url2)

    resp = await auth_client.post("/flow/register", json={"url": [url1, url2]})
    assert resp.status_code == 201
    endpoints = resp.json()
    assert [ep["summary"] for ep in endpoints] == ["Factory Example", "Get Example", "Model Example"]
    resp = await auth_client.get("/flow")
    assert endpoints == [ep for ep in resp.json()["items"]]
    assert [ep["deprecated"] for ep in endpoints] == [False, True, False]
    proc1.terminate()
    proc1.join() 
    proc2.terminate()
    proc2.join() 


@pytest.mark.asyncio
async def test_flow_register_ray(auth_client):

    app = create_app()
    @serve.deployment
    @serve.ingress(app)
    class FormText: pass

    fast_app = FormText.bind()  # type: ignore
    serve.run(fast_app, route_prefix="/ray-test")
    serve.status()
    resp = await auth_client.post("/flow/register", json={"url": "http://localhost:8000/-/routes"})
    assert resp.status_code == 201
    endpoints = resp.json()
    assert [ep["summary"] for ep in endpoints] == ["Factory Example", "Get Example"]
    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == len(endpoints)
    assert items[0]["summary"] == "Factory Example"
    assert items[0]["tags"] == ["flow"]
    assert items[0]["method"] == "GET"
    assert items[0]["url"] == '/-/localhost/8000/ray-test/-/'
    assert items[0]["source"] == 'http://localhost:8000/ray-test/openapi.json'
    assert items[0]["description"] == "Factory Description"
    assert items[0]["deprecated"] is False
    assert items[0]["author"] == "Factory Author"
    assert items[0]["organization"] == "Factory Organization"
    assert items[1]["summary"] == "Get Example"
    assert items[1]["tags"] == []
    assert items[1]["method"] == "GET"
    assert items[1]["url"] == "/-/localhost/8000/ray-test/-/get"
    assert items[1]["source"] == 'http://localhost:8000/ray-test/openapi.json'
    assert items[1]["description"] == "Get Description"
    assert items[1]["deprecated"] is True
    serve.shutdown()

@pytest.mark.asyncio
async def test_flow_register_ray_deep(auth_client):

    app = create_app()
    @serve.deployment
    @serve.ingress(app)
    class FormText: pass

    fast_app = FormText.bind()  # type: ignore
    serve.run(fast_app, route_prefix="/ray-test/deep")
    serve.status()
    resp = await auth_client.post("/flow/register", json={"url": "http://localhost:8000/-/routes"})
    assert resp.status_code == 201
    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    items = resp.json()["items"]
    resp = await auth_client.get("/-/localhost/8000/ray-test/deep/-/")
    assert resp.status_code == 200
    resp = await auth_client.get("/-/localhost/8000/ray-test/deep/-/get")
    assert resp.status_code == 200
    serve.shutdown()


@pytest.mark.asyncio
async def test_flow_register_three(auth_client):
    proc1 = Process(target=run_uvicorn, args=("tests.test_flow:create_app", 8125,))
    proc1.start()
    proc2 = Process(target=run_uvicorn, args=("tests.test_flow:app", 8126,))
    proc2.start()
    url1 = f"http://localhost:8125/openapi.json"
    url2 = f"http://localhost:8126/openapi.json"
    await wait_for(url1)
    await wait_for(url2)

    resp = await auth_client.post("/flow/register", json={"url": [url1, url2]})
    assert resp.status_code == 201

    app = create_app()
    @serve.deployment
    @serve.ingress(app)
    class FormText: pass

    fast_app = FormText.bind()  # type: ignore
    serve.run(fast_app, route_prefix="/ray-test")
    serve.status()
    resp = await auth_client.post("/flow/register", json={"url": "http://localhost:8000/-/routes"})
    assert resp.status_code == 201
    endpoints = resp.json()
    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    items = resp.json()["items"]
    expect = [
        '/-/localhost/8000/ray-test/-/', 
        '/-/localhost/8000/ray-test/-/get', 
        '/-/localhost/8125/-/', 
        '/-/localhost/8125/-/get', 
        '/-/localhost/8126/-/'
    ]
    assert sorted([ep["url"] for ep in items]) == expect
    for url in expect:
        resp = await auth_client.get(url)
        assert resp.status_code == 200

    # Unregister http://localhost:8125/openapi.json
    resp = await auth_client.post("/flow/unregister", json={"url": url1})
    assert resp.status_code == 200
    assert resp.json() == {"deletes": [url1]}

    # Verify that the flows from port 8125 are removed
    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    items = resp.json()["items"]
    expect = [
        '/-/localhost/8000/ray-test/-/', 
        '/-/localhost/8000/ray-test/-/get', 
        '/-/localhost/8126/-/'
    ]
    assert sorted([ep["url"] for ep in items]) == expect

    for proc in (proc1, proc2):
        proc.terminate()
        proc.join() 

    proc3 = Process(target=run_uvicorn, args=("tests.test_flow:create_app", 
                                               8126,))
    proc3.start()
    url3 = f"http://localhost:8126/openapi.json"
    await wait_for(url3)

    resp = await auth_client.put("/flow/register")
    assert resp.status_code == 200

    resp = await auth_client.get("/flow")
    assert resp.status_code == 200
    items = resp.json()["items"]
    expect = [
        '/-/localhost/8000/ray-test/-/', 
        '/-/localhost/8000/ray-test/-/get', 
        '/-/localhost/8126/-/', 
        '/-/localhost/8126/-/get'
    ]
    assert sorted([ep["url"] for ep in items]) == expect

    for url in expect:
        resp = await auth_client.get(url)
        assert resp.status_code == 200

    proc3.terminate()
    proc3.join() 

    serve.shutdown()

def create_app1():
    app = ServeAPI()
    form_model = Model(
        InputText(label="Name", name="name", placeholder="Enter your name"),
        Checkbox(label="Active", name="active", option="ACTIVE", value=False),
        Submit("Submit"),
        Cancel("Cancel"),
    )
    class FormData(BaseModel):
        name: str
        active: bool = False

    @app.enter(
        "/",
        model=form_model,
        summary="Root Example"
    )
    async def root(data: FormData, request: Request) -> dict:
        return {"result": data.model_dump()}

    @app.enter(
        "/level1",
        model=form_model,
        summary="Level 1 Example"
    )
    async def level1(data: FormData, request: Request) -> dict:
        return {"result": data.model_dump()}

    @app.enter(
        "/level2/deep",
        model=form_model,
        summary="Level 2 Example"
    )
    async def level2(data: FormData, request: Request) -> dict:
        return {"result": data.model_dump()}

    @app.get(
        "/even/much/deper",
        entry=True,
        summary="Get Example"
    )
    async def dep(request: Request) -> dict:
        return {"result": None}

    return app



@pytest.mark.asyncio
async def test_flow_register_real_deep(auth_client):
    port = 8124
    proc = Process(target=run_uvicorn, args=("tests.test_flow:create_app1", port,))
    proc.start()
    import httpx
    url = f"http://localhost:{port}/openapi.json"
    async with httpx.AsyncClient() as client:
        for _ in range(40):  # max ~10 s
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        else:
            pytest.fail("uvicorn-App konnte nicht gestartet werden")

    resp = await auth_client.post("/flow/register", json={"url": url})
    assert resp.status_code == 201
    endpoints = resp.json()
    # assert [ep["summary"] for ep in endpoints] == ["Factory Example", "Get Example"]
    # resp = await auth_client.get("/flow")
    # assert resp.status_code == 200
    # items = resp.json()["items"]
    # assert len(items) == len(endpoints)
    # assert items[0]["summary"] == "Factory Example"
    # assert items[0]["tags"] == ["flow"]
    # assert items[0]["method"] == "GET"
    # assert items[0]["url"] == "/-/localhost/8124/-/"
    # assert items[0]["source"] == 'http://localhost:8124/openapi.json'
    # assert items[0]["description"] == "Factory Description"
    # assert items[0]["deprecated"] is False
    # assert items[0]["author"] == "Factory Author"
    # assert items[0]["organization"] == "Factory Organization"
    # assert items[1]["summary"] == "Get Example"
    # assert items[1]["tags"] == []
    # assert items[1]["method"] == "GET"
    # assert items[1]["url"] == "/-/localhost/8124/-/get"
    # assert items[1]["source"] == 'http://localhost:8124/openapi.json'
    # assert items[1]["description"] == "Get Description"
    # assert items[1]["deprecated"] is True
    proc.terminate()
    proc.join() 
