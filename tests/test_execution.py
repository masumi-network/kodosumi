import pytest
import ray
import asyncio
from multiprocessing import Process
import kodosumi.spooler
import httpx
import re

from fastapi import Request, Response
from pydantic import BaseModel
from kodosumi.core import ServeAPI, Launch, Tracer
from kodosumi.service.inputs.forms import (Model, InputText, Checkbox, Submit, 
                                           Cancel, Markdown, InputFiles)
from kodosumi.service.inputs.errors import InputsError
from kodosumi.const import STATUS_FINAL

def run_uvicorn(factory: str, port: int):
    import uvicorn
    uvicorn.run(
        factory,
        host="localhost",
        port=port,
        reload=False
    )


async def runner_0(inputs: dict, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug()
    # listing = await tracer.list_file()
    # if listing == []:
    #     tracer.get_file("image_data.bin")
    # else:
    #     chunks = []
    #     async for chunk in tracer.get_file("image_data.bin"):
    #         chunks.append(chunk)
    #     file = b"".join(chunks)
    #     assert file == b"BINARY_IMAGE_DATA_" * 10 * 1024 * 1024
    return {"runner_0_result": "ok"}


async def runner(inputs: dict, tracer: Tracer):
    await tracer.debug("this is a debug message")
    print("this is stdout")
    result = await tracer.lock("lock-1", data={"hello": "from runner"})
    # await asyncio.sleep(3)
    return {"lock-result": result}


async def runner_2(inputs: dict, tracer: Tracer):
    await tracer.debug("this is a debug message 2")
    print("this is stdout 2")
    result = await tracer.lock("lock-1", data={"hello": "from runner 2",
                                               "inputs": inputs})
    # await asyncio.sleep(3)
    return {"lock-result": result}

async def runner_3(inputs: dict, tracer: Tracer):
    await tracer.debug("this is a debug message 2")
    print("this is stdout 3")
    result = await tracer.lock("lock-2", 
                               data={"hello": "from runner 3",
                                     "inputs": inputs
                               }, timeout=3)
    return {"lock-result": result}

async def runner_4(inputs: dict, tracer: Tracer):
    result = await tracer.lock("lock-3", 
                               data={"hello": "from runner 4",
                                     "inputs": inputs
                               })
    return {"lock-result": result}

def app_factory():
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
    async def post(inputs: dict, request: Request) -> dict:
        """Echo-Endpunkt, der die Eingaben zurückliefert."""
        return Launch(request, "tests.test_execution:runner", inputs=inputs)

    @app.lock("lock-1")
    async def lock_1():
        return Model(
            Markdown("# hello world"),
            Checkbox(label="Continue", name="continue",
                     option="CONTINUE", value=False),
            Submit("yes"),
            Cancel("no"),
        )

    @app.lease("lock-1")
    async def lease_1(inputs: dict):
        return {"phase": "lease-1", "inputs": inputs}

    @app.enter(
        "/much/deeper",
        model=form_model
    )
    async def post2(inputs: dict, request: Request) -> dict:
        return Launch(request, "tests.test_execution:runner", inputs=inputs)

    return app


def app_factory_2():
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
        summary="Factory Example 2",
        tags=["flow"],
        organization="Factory Organization 2",
        author="Factory Author 2",
        deprecated=False,
        description="Factory Description 2",
    )
    async def post1(inputs: dict, request: Request) -> dict:
        return Launch(request, "tests.test_execution:runner_2", inputs=inputs)

    @app.lock("lock-1")
    async def lock_1(data: dict):
        return Model(
            Markdown(f"# hello world {data['hello']}"),
            Checkbox(label="Continue", name="continue",
                     option="CONTINUE", value=False),
            InputText(label="Name", name="name",
                      placeholder="Enter another name"),
            Submit("yes"),
            Cancel("no"),
        )

    @app.lease("lock-1")
    async def lease_1(inputs: dict):
        return {"phase": "lease-1", "inputs": inputs, "outputs": "hello world"}

    @app.enter(
        "/timeout",
        model=form_model,
        summary="Factory Example 2 Timeout",
        tags=["flow"],
        organization="Factory Organization 2",
        author="Factory Author 2",
        deprecated=False,
        description="Factory Description 2",
    )
    async def post2(inputs: dict, request: Request) -> dict:
        return Launch(request, "tests.test_execution:runner_3", inputs=inputs)

    @app.lock("lock-2")
    async def lock_2(data: dict):
        return Model(
            Markdown(f"# hello world {data['hello']}"),
            Checkbox(label="Continue", name="continue",
                     option="CONTINUE", value=False),
            InputText(label="Name", name="name",
                      placeholder="Enter another name"),
            Submit("yes"),
            Cancel("no"),
        )

    @app.lease("lock-2")
    async def lease_2(inputs: dict):
        return {"phase": "lease-2", "inputs": inputs, "outputs": "hello world"}

    @app.enter(
        "/post3",
        model=form_model,
        summary="Factory Example 3 Failed",
        tags=["flow"],
        organization="Factory Organization 3",
        author="Factory Author 3",
        deprecated=False,
        description="Factory Description 3",
    )
    async def post3(inputs: dict, request: Request) -> dict:
        return Launch(request, "tests.test_execution:runner_4", inputs=inputs)

    @app.lock("lock-3")
    async def lock_3(data: dict):
        return Model(
            Markdown(f"# hello world {data['hello']}"),
            Checkbox(label="Continue", name="continue",
                     option="CONTINUE", value=False),
            InputText(label="Name", name="name",
                      placeholder="say yes to continue"),
            Submit("yes"),
            Cancel("no"),
        )

    @app.lease("lock-3")
    async def lease_3(inputs: dict):
        # fail
        error = InputsError()
        if not inputs.get("continue"):
            error.add(**{"continue": "Continue must be checked"})
        if inputs.get("name") != "yes":
            error.add(name="Name must be 'yes'")
        if error.has_errors():
            raise error
        return {"phase": "lease-3", "inputs": inputs, "outputs": "hello world"}

    return app

def app_factory_3():

    app = ServeAPI()

    @app.enter(
        "/simple",
        model=Model(
            InputText(label="Name", name="name", placeholder="Enter your name"),
            InputFiles(label="Upload Files", name="files", multiple=True, 
                       directory=False, required=True),
            Submit("Submit"),
            Cancel("Cancel"),
        ),
        summary="Simple Example 3",
        description="launches runner_2",
    )
    async def simple(inputs: dict, request: Request) -> Launch:
        return Launch(request, "tests.test_execution:runner_0", inputs=inputs)

    @app.enter(
        "/error",
        model=Model(Submit("Submit")),
        summary="Error Raiser",
        description="no launch",
    )
    async def throw(inputs: dict, request: Request) -> Launch:
        error = InputsError()
        error.flash("This is not allowed and does not launch")
        raise error

    @app.enter(
        "/wrong",
        model=Model(Submit("Submit")),
        summary="Wrong Returns",
        description="no launch",
    )
    async def wrong(inputs: dict, request: Request) -> dict:
        return {"result": "wrong"}

    @app.enter(
        "/except",
        model=Model(Submit("Submit")),
        summary="Exception",
        description="no launch",
    )
    async def exception(inputs: dict, request: Request) -> dict:
        raise RuntimeError("This is a runtime error")

    @app.enter(
        "/response",
        model=Model(Submit("Submit")),
        summary="Response Error",
        description="no launch",
    )
    async def return_response(inputs: dict, request: Request) -> Response:
        return Response(content="hi")   

    @app.enter(
        "/header",
        model=Model(Submit("Submit")),
        summary="Header Returns",
        description="no launch",
    )
    async def wrong_headers(inputs: dict, request: Request) -> dict:
        return {"headers": {"fid": None}}

    return app

app4 = ServeAPI()

@app4.enter(
    "/simple",
    model=Model(
        InputText(label="Name", name="name", placeholder="Enter your name"),
        Submit("Submit"),
        Cancel("Cancel"),
    ),
    summary="Simple Example 4",
    description="launches runner_2",
)
async def simple(inputs: dict, request: Request) -> Launch:
    return Launch(request, "tests.test_execution:runner_0", inputs=inputs)

@pytest.fixture
def app_server():
    proc = Process(
        target=run_uvicorn,
        args=("tests.test_execution:app_factory", 8125,))
    proc.start()
    yield f"http://localhost:8125"
    proc.kill()
    proc.terminate()
    proc.join()


@pytest.fixture
def app_server2():
    proc = Process(
        target=run_uvicorn,
        args=("tests.test_execution:app_factory_2", 8125,))
    proc.start()
    yield f"http://localhost:8125"
    proc.kill()
    proc.terminate()
    proc.join()


@pytest.fixture
def app_server3():
    proc = Process(
        target=run_uvicorn,
        args=("tests.test_execution:app_factory_3", 8125,))
    proc.start()
    yield f"http://localhost:8125"
    proc.kill()
    proc.terminate()
    proc.join()

@pytest.fixture
def app_server4():
    proc = Process(
        target=run_uvicorn,
        args=("tests.test_execution:app4", 8125,))
    proc.start()
    yield f"http://localhost:8125"
    proc.kill()
    proc.terminate()
    proc.join()


@pytest.fixture
def spooler_server():
    proc = Process(target=kodosumi.spooler.run)
    proc.start()
    yield
    proc.kill()
    proc.join()


@pytest.fixture
def koco_server():
    proc = Process(
        target=run_uvicorn,
        args=("kodosumi.service.app:create_app", 8126,))
    proc.start()
    yield f"http://localhost:8126"
    actor = ray.get_actor("register", namespace="kodosumi")
    if actor:
        ray.kill(actor)
    proc.kill()
    proc.join()


@pytest.mark.asyncio
async def test_lock_lease(app_server, spooler_server, koco_server):
    async with httpx.AsyncClient() as client:
        for _ in range(40):  # max ~10 s
            try:
                resp = await client.get(
                    f"{koco_server}/login?name=admin&password=admin")
                if resp.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)

        resp = await client.post(f"{koco_server}/flow/register",
                                 json={"url": [f"{app_server}/openapi.json"]})
        assert resp.status_code == 201

        endpoints = resp.json()
        assert [ep["summary"] for ep in endpoints] == [
            "Factory Example", "Get Form Schema"]
        resp = await client.get(f"{koco_server}/flow")
        assert endpoints == [ep for ep in resp.json()["items"]]
        assert [ep["deprecated"] for ep in endpoints] == [False, False]

        resp = await client.get(f"{koco_server}/inputs" + endpoints[0]["url"])
        assert resp.status_code == 200
        form_data = {
            "name": "Test User",
            "active": "on"
        }
        resp = await client.post(
            f"{koco_server}/inputs" + endpoints[0]["url"], data=form_data)
        assert resp.status_code == 302
        url = resp.headers.get("location")
        fid = url.split("/")[-1]
        while True:
            actor = ray.get_actor(fid, namespace="kodosumi")
            oid = actor.get_locks.remote()
            locks = ray.get(oid)
            if not locks:
                await asyncio.sleep(0.25)
                continue
            for lid, data in locks.items():
                await actor.lease.remote(lid, {"hello": "world"})
            break
        while True:
            resp = await client.get(f"{koco_server}/outputs/raw/{fid}")
            if resp.status_code == 200:
                if resp.json() == {"dict": {'lock-result': {'hello': 'world'}}}:
                    break
            await asyncio.sleep(0.25)
        while True:
            resp = await client.get(f"{koco_server}/outputs/status/{fid}")
            if resp.status_code == 200:
                if resp.json().get("status") == "finished":
                    break
            await asyncio.sleep(0.25)


async def _prep_flow_register(app_server, spooler_server, koco_server):
    client = httpx.AsyncClient()
    for _ in range(40):  # max ~10 s
        try:
            resp = await client.get(
                f"{koco_server}/login?name=admin&password=admin")
            if resp.status_code == 200:
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    resp = await client.post(f"{koco_server}/flow/register",
                             json={"url": [f"{app_server}/openapi.json"]})
    assert resp.status_code == 201

    endpoints = resp.json()

    resp = await client.get(f"{koco_server}/inputs" + endpoints[0]["url"])
    assert resp.status_code == 200
    form_data = {
        "name": "Test User",
        "active": "on"
    }
    resp = await client.post(
        f"{koco_server}/inputs" + endpoints[0]["url"], data=form_data)
    assert resp.status_code == 302
    url = resp.headers.get("location")
    fid = url.split("/")[-1]
    return client, app_server, spooler_server, koco_server, fid


@pytest.mark.asyncio
async def test_lock_result(app_server, spooler_server, koco_server):
    client, *_, fid = await _prep_flow_register(
        app_server, spooler_server, koco_server)
    while True:
        actor = ray.get_actor(fid, namespace="kodosumi")
        oid = actor.get_locks.remote()
        locks = ray.get(oid)
        if not locks:
            await asyncio.sleep(0.25)
            continue
        for lid, data in locks.items():
            await actor.lease.remote(lid, {"hello": "world"})
        break
    while True:
        resp = await client.get(f"{koco_server}/outputs/raw/{fid}")
        if resp.status_code == 200:
            if resp.json() == {"dict": {'lock-result': {'hello': 'world'}}}:
                break
        await asyncio.sleep(0.25)
    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}")
        if resp.status_code == 200:
            if resp.json().get("status") == "finished":
                break
        await asyncio.sleep(0.25)
    await client.aclose()


@pytest.mark.asyncio
async def test_get_lock(app_server, spooler_server, koco_server):
    client, *_, fid = await _prep_flow_register(
        app_server, spooler_server, koco_server)
    while True:
        try:
            resp = await client.get(f"{koco_server}/outputs/status/{fid}")
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status == "awaiting":
                    locks = resp.json().get("locks")
                    lid = locks[0]
                    if lid:
                        break
        except Exception:
            pass
        await asyncio.sleep(0.25)
    resp = await client.get(f"{koco_server}/lock/x88394e76dd65923d09d5394/6c0fff81-2377-4b4a-b040-794c24c1714c")
    assert resp.status_code == 404
    assert resp.json().get("detail") == 'Execution x88394e76dd65923d09d5394 not found.'
    resp = await client.get(f"{koco_server}/lock/{fid}/6c0fff81-2377-4b4a-b040-794c24c1714c")
    assert resp.status_code == 404
    assert resp.json().get(
        "detail") == f'Lock 6c0fff81-2377-4b4a-b040-794c24c1714c for {fid} not found.'
    resp = await client.get(f"{koco_server}/lock/{fid}/{lid}")
    assert resp.status_code == 200
    expected = [
        {"type": "markdown", "text": "# hello world"},
        {"type": "boolean", "label": "Continue", "name": "continue",
            "option": "CONTINUE", "value": False},
        {"type": "submit", "text": "yes"},
        {"type": "cancel", "text": "no"},
    ]
    assert resp.json() == expected
    resp = await client.get(f"{app_server}/_lock_/{fid}/{lid}")
    assert resp.status_code == 200
    assert resp.json() == expected
    resp = await client.get(f"{koco_server}/inputs/lock/{fid}/{lid}")
    assert resp.status_code == 200
    resp = await client.delete(f"{koco_server}/outputs/{fid}")
    assert resp.status_code == 204
    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}")
        if resp.status_code == 404:
            break
        await asyncio.sleep(0.25)
    await client.aclose()


@pytest.mark.asyncio
async def test_get_lock_deep(app_server, spooler_server, koco_server):
    client = httpx.AsyncClient()
    for _ in range(40):  # max ~10 s
        try:
            resp = await client.get(
                f"{koco_server}/login?name=admin&password=admin")
            if resp.status_code == 200:
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    resp = await client.post(f"{koco_server}/flow/register",
                             json={"url": [f"{app_server}/openapi.json"]})
    assert resp.status_code == 201

    endpoints = resp.json()

    resp = await client.get(f"{koco_server}/inputs" + endpoints[1]["url"])
    assert resp.status_code == 200
    form_data = {
        "name": "Test User",
        "active": "on"
    }
    resp = await client.post(
        f"{koco_server}/inputs" + endpoints[1]["url"], data=form_data)
    assert resp.status_code == 302
    url = resp.headers.get("location")
    fid = url.split("/")[-1]
    while True:
        try:
            resp = await client.get(f"{koco_server}/outputs/status/{fid}")
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status == "awaiting":
                    locks = resp.json().get("locks")
                    lid = locks[0]
                    if lid:
                        break
        except Exception:
            pass
        await asyncio.sleep(0.25)
    resp = await client.get(f"{koco_server}/lock/x88394e76dd65923d09d5394/6c0fff81-2377-4b4a-b040-794c24c1714c")
    assert resp.status_code == 404
    assert resp.json().get("detail") == 'Execution x88394e76dd65923d09d5394 not found.'
    resp = await client.get(f"{koco_server}/lock/{fid}/6c0fff81-2377-4b4a-b040-794c24c1714c")
    assert resp.status_code == 404
    assert resp.json().get(
        "detail") == f'Lock 6c0fff81-2377-4b4a-b040-794c24c1714c for {fid} not found.'
    resp = await client.get(f"{koco_server}/lock/{fid}/{lid}")
    assert resp.status_code == 200
    resp = await client.get(f"{app_server}/_lock_/{fid}/{lid}")
    assert resp.status_code == 200
    assert resp.json() == [
        {"type": "markdown", "text": "# hello world"},
        {"type": "boolean", "label": "Continue", "name": "continue",
            "option": "CONTINUE", "value": False},
        {"type": "submit", "text": "yes"},
        {"type": "cancel", "text": "no"},
    ]
    resp = await client.get(f"{koco_server}/inputs/lock/{fid}/{lid}")
    assert resp.status_code == 200
    html = resp.text.replace("\n", "")
    assert """<h1 id="hello-world">hello world</h1>""" in html
    assert """<button type="submit">yes</button>""" in html
    assert """<a class="button" href="javascript:history.back()">no</a>""" in html
    #assert """<a class="button" href="/">no</a>""" in html

    resp = await client.delete(f"{koco_server}/outputs/{fid}")
    assert resp.status_code == 204
    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}")
        if resp.status_code == 404:
            break
        await asyncio.sleep(0.25)

    await client.aclose()


@pytest.mark.asyncio
async def test_get_lock_post(app_server, spooler_server, koco_server):
    client = httpx.AsyncClient()
    for _ in range(40):  # max ~10 s
        try:
            resp = await client.get(
                f"{koco_server}/login?name=admin&password=admin")
            if resp.status_code == 200:
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    resp = await client.post(f"{koco_server}/flow/register",
                             json={"url": [f"{app_server}/openapi.json"]})
    assert resp.status_code == 201
    endpoints = resp.json()
    form_data = {
        "name": "Test User",
        "active": "on"
    }
    resp = await client.post(
        f"{koco_server}/inputs" + endpoints[1]["url"], data=form_data)
    assert resp.status_code == 302
    url = resp.headers.get("location")
    fid = url.split("/")[-1]
    while True:
        try:
            resp = await client.get(f"{koco_server}/outputs/status/{fid}")
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status == "awaiting":
                    locks = resp.json().get("locks")
                    lid = locks[0]
                    if lid:
                        break
        except Exception:
            pass
        await asyncio.sleep(0.25)
    resp = await client.get(f"{koco_server}/lock/{fid}/{lid}")
    assert resp.status_code == 200
    elements = [
        {"type": "markdown", "text": "# hello world"},
        {"type": "boolean", "label": "Continue", "name": "continue",
            "option": "CONTINUE", "value": False},
        {"type": "submit", "text": "yes"},
        {"type": "cancel", "text": "no"},
    ]
    assert resp.json() == elements
    form_data = {
        "name": "Test User",
        "active": "off"
    }
    resp = await client.post(f"{koco_server}/lock/{fid}/{lid}", json=form_data,
                             timeout=120)
    assert resp.status_code == 200
    # assert resp.json()["elements"] == elements
    result = {
        'inputs': {
            'active': 'off',
            'continue': False,
            'name': 'Test User'
        },
        'phase': 'lease-1'
    }
    assert resp.json()["result"] == result
    resp = await client.get(f"{koco_server}/lock/{fid}/{lid}")
    assert resp.status_code == 404
    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}")
        if resp.status_code == 200:
            status = resp.json().get("status")
            if status == "finished":
                break
    await client.aclose()

async def _prep_factory2(app_server2, spooler_server, koco_server, url_index):
    client = httpx.AsyncClient()
    for _ in range(40):  # max ~10 s
        try:
            resp = await client.get(
                f"{koco_server}/login?name=admin&password=admin")
            if resp.status_code == 200:
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    resp = await client.post(f"{koco_server}/flow/register",
                             json={"url": [f"{app_server2}/openapi.json"]},
                             timeout=300)
    assert resp.status_code == 201

    endpoints = resp.json()
    assert len(endpoints) == 3
    assert endpoints[0]["summary"] == "Factory Example 2"
    assert endpoints[1]["summary"] == "Factory Example 2 Timeout"
    assert endpoints[2]["summary"] == "Factory Example 3 Failed"

    url = endpoints[url_index]["url"]
    form_data = {
        "name": "Test User",
        "active": "on"
    }
    resp = await client.post(
        f"{koco_server}/inputs" + url, data=form_data, timeout=300)
    assert resp.status_code == 302
    url = resp.headers.get("location")
    fid = url.split("/")[-1]
    lid = None
    while True:
        try:
            resp = await client.get(f"{koco_server}/outputs/status/{fid}")
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status == "awaiting":
                    locks = resp.json().get("locks")
                    lid = locks[0]
                    if lid:
                        break
        except Exception:
            pass
        await asyncio.sleep(0.25)
    resp = await client.get(f"{koco_server}/lock/{fid}/{lid}", timeout=120)
    assert resp.status_code == 200
    return client, fid, lid, resp.json()


@pytest.mark.asyncio
async def test_lock_data(app_server2, spooler_server, koco_server):
    client, fid, lid, model = await _prep_factory2(
        app_server2, spooler_server, koco_server, 0)
    expected = [
        {
            'type': 'markdown',
            'text': '# hello world from runner 2'
        },
        {
            'type': 'boolean',
            'name': 'continue',
            'label': 'Continue',
            'value': False,
            'option': 'CONTINUE'
        },
        {
            'type': 'text',
            'name': 'name',
            'label': 'Name',
            'value': None,
            'required': False,
            'placeholder': 'Enter another name',
            'size': None,
            'pattern': None
        },
        {
            'type': 'submit',
            'text': 'yes'
        },
        {
            'type': 'cancel',
            'text': 'no'
        }
    ]
    assert model == expected
    form_data = {
        "name": "Test User",
        "continue": "on"
    }
    resp = await client.post(f"{koco_server}/lock/{fid}/{lid}",
                             json=form_data, timeout=120)
    assert resp.status_code == 200
    # assert resp.json()["elements"] == expected
    result = { 
        'inputs': {
            'continue': True, 
            'name': 'Test User'
        },
        'outputs': 'hello world',
        'phase': 'lease-1'
    }
    assert resp.json()["result"] == result
    resp = await client.get(f"{koco_server}/lock/{fid}/{lid}", timeout=120)
    assert resp.status_code == 404
    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}")
        if resp.status_code == 200:
            status = resp.json().get("status")
            if status == "finished":
                break
    await client.aclose()


@pytest.mark.asyncio
async def test_lock_timeout(app_server2, spooler_server, koco_server):
    client, fid, lid, model = await _prep_factory2(
        app_server2, spooler_server, koco_server, 1)
    expected = [
        {
            'type': 'markdown',
            'text': '# hello world from runner 3'
        },
        {
            'type': 'boolean',
            'name': 'continue',
            'label': 'Continue',
            'value': False,
            'option': 'CONTINUE'
        },
        {
            'type': 'text',
            'name': 'name',
            'label': 'Name',
            'value': None,
            'required': False,
            'placeholder': 'Enter another name',
            'size': None,
            'pattern': None
        },
        {
            'type': 'submit',
            'text': 'yes'
        },
        {
            'type': 'cancel',
            'text': 'no'
        }
    ]
    assert model == expected
    while True:
        resp = await client.get(f"{koco_server}/lock/{fid}/{lid}", timeout=120)
        if resp.status_code == 404:
            break
        await asyncio.sleep(0.25)
    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}", timeout=120)
        if resp.status_code == 200:
            status = resp.json().get("status")
            if status == "error":
                break
        await asyncio.sleep(0.25)
    resp = await client.get(f"{koco_server}/outputs/main/{fid}", timeout=120)
    assert "event: lock" in resp.text
    assert f"data: TimeoutError: Lock {lid} expired" in resp.text
    assert re.search(r"event: status\s+data: [\d.]+:error", resp.text)

    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}")
        if resp.json().get("status") == "error":
            break
        await asyncio.sleep(0.25)

    await client.aclose()


@pytest.mark.asyncio
async def test_lock_stream(app_server2, spooler_server, koco_server):
    client, fid, lid, model = await _prep_factory2(
        app_server2, spooler_server, koco_server, 0)
    form_data = {
        "name": "Test User",
        "continue": "on"
    }
    resp = await client.post(f"{koco_server}/lock/{fid}/{lid}",
                             json=form_data, timeout=120)
    assert resp.status_code == 200
    
    async with client.stream('GET', f"{koco_server}/outputs/main/{fid}", timeout=120) as resp:
        lock = False
        async for line in resp.aiter_lines():
            print(line)
            if line.strip() == "event: lock":
                lock = True
            elif line.strip() == "event: lease":
                if not lock:
                    raise Exception("Lock event before lease event")
    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}")
        if resp.json().get("status") == "finished":
            break
        await asyncio.sleep(0.25)
    await client.aclose()

@pytest.mark.asyncio
async def test_lease_failed(app_server2, spooler_server, koco_server):
    client, fid, lid, model = await _prep_factory2(
        app_server2, spooler_server, koco_server, 2)
    form_data = {
        "name": "no",
        "continue": "off"
    }
    resp = await client.post(f"{koco_server}/lock/{fid}/{lid}",
                             json=form_data, timeout=120)
    assert resp.status_code == 200
    expected = {
        'continue': ['Continue must be checked'], 
        'name': ["Name must be 'yes'"], '_global_': []
    }
    assert resp.json()["errors"] == expected

    form_data = {
        "name": "yes",
        "continue": "on"
    }
    resp = await client.post(f"{koco_server}/lock/{fid}/{lid}",
                             json=form_data, timeout=120)
    assert resp.status_code == 200

    async with client.stream('GET', f"{koco_server}/outputs/main/{fid}", timeout=120) as resp:
        lock = False
        async for line in resp.aiter_lines():
            print(line)
            if line.strip() == "event: lock":
                lock = True
            elif line.strip() == "event: lease":
                if not lock:
                    raise Exception("Lock event before lease event")
    while True:
        resp = await client.get(f"{koco_server}/outputs/status/{fid}")
        if resp.json().get("status") == "finished":
            break
        await asyncio.sleep(0.25)

    await client.aclose()

async def register_flow(app_server, koco_server):
    client = httpx.AsyncClient()
    for _ in range(40):  # max ~10 s
        try:
            resp = await client.get(
                f"{koco_server}/login?name=admin&password=admin")
            if resp.status_code == 200:
                break
        except Exception:
            pass
        await asyncio.sleep(0.25)

    resp = await client.post(f"{koco_server}/flow/register",
                                json={"url": [f"{app_server}/openapi.json"]},
                                timeout=300)
    assert resp.status_code == 201
    endpoints = resp.json()
    return client, endpoints

async def wait_for_job(client, koco_server, fid, final=STATUS_FINAL):
    while True:
        try:
            resp = await client.get(f"{koco_server}/outputs/status/{fid}")
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status in final:
                    return status
        except Exception:
            pass
        await asyncio.sleep(0.25)

@pytest.mark.asyncio
async def test_simple_factory(app_server3, spooler_server, koco_server):
    client, _ = await register_flow(app_server3, koco_server)
    resp = await client.post(f"{koco_server}/-/localhost/8125/-/simple",
                             timeout=300)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    assert fid is not None
    status = await wait_for_job(client, koco_server, fid)
    assert status == "finished"

@pytest.mark.asyncio
async def test_factory_errors(app_server3, spooler_server, koco_server):
    client, endpoints = await register_flow(app_server3, koco_server)
    assert [e["summary"] for e in endpoints] == sorted([
        'Simple Example 3', 'Error Raiser', 'Wrong Returns', 'Exception',
        'Response Error', 'Header Returns'])

    resp = await client.post(f"{koco_server}/-/localhost/8125/-/error",
                             timeout=300)
    assert resp.status_code == 200
    assert resp.json()["errors"] == {
        "_global_": ["This is not allowed and does not launch"]
    }

    resp = await client.post(f"{koco_server}/-/localhost/8125/-/wrong",
                             timeout=300)
    assert resp.status_code == 400
    assert resp.json() == {
        'detail': 'kodosumi endpoint must return a Launch object or raise an InputsError'
    }

    resp = await client.post(f"{koco_server}/-/localhost/8125/-/header",
                             timeout=300)
    assert resp.status_code == 400
    assert resp.json() == {
        'detail': 'kodosumi endpoint must return a Launch object or raise an InputsError'
    }

    resp = await client.post(f"{koco_server}/-/localhost/8125/-/except",
                             timeout=300)
    assert resp.status_code == 500
    assert resp.json() == {
        'detail': "RuntimeError('This is a runtime error')"
    }

    resp = await client.post(f"{koco_server}/-/localhost/8125/-/response",
                             timeout=300)
    assert resp.status_code == 400
    assert resp.json() == {
        'detail': 'kodosumi endpoint must return a Launch object or raise an InputsError'
    }

    await client.aclose()


@pytest.mark.asyncio
async def test_simple_global(app_server4, spooler_server, koco_server):
    client, endpoints = await register_flow(app_server4, koco_server)
    assert [e["summary"] for e in endpoints] == ['Simple Example 4']
    await client.aclose()


