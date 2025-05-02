import asyncio
import os
from multiprocessing import Process

import httpx
import pytest
from ray import serve

import kodosumi.spooler
import tests.apps.serve_app
from kodosumi.config import Settings
from tests.test_role import auth_client


@pytest.fixture
def start_spooler(tmp_path):
    settings = Settings()
    settings.EXEC_DIR = str(tmp_path.joinpath("data"))
    proc = Process(target=kodosumi.spooler.main, args=(settings,), kwargs={})
    proc.start()
    yield
    proc.kill()


@pytest.fixture
def start_ray():
    os.system("ray start --head")
    yield
    os.system("ray stop")


async def test_serve_run(start_ray, start_spooler, auth_client):
    resp = await auth_client.get("/schema/openapi.json")
    assert resp.status_code == 200
    # ray has been started automatically
    resp = httpx.get("http://localhost:8265")
    assert resp.status_code == 200
    # start app on 8000
    serve.run(tests.apps.serve_app.fast_app)
    serve.status()
    resp = await auth_client.post(
        "/flow/register", json={"url": "http://localhost:8000/openapi.json"})
    assert resp.status_code == 201
    js = resp.json()
    resp = await auth_client.get(js[0]["url"])
    assert resp.status_code == 200
    assert "<html>" in resp.content.decode()
    assert "</html>" in resp.content.decode()
    resp = await auth_client.get("/flow")
    js = resp.json()
    assert js["items"][0]["method"] == "GET"
    resp = await auth_client.get(js["items"][0]["url"])
    assert resp.status_code == 200
    assert "<html>" in resp.content.decode()
    assert "</html>" in resp.content.decode()
    assert js["items"][1]["method"] == "POST"

    async def _wait(f):
        while True:
            resp = await auth_client.get(f"/exec/state/{f}")
            assert resp.status_code == 200
            if resp.json()["status"] == "finished":
                break
            await asyncio.sleep(0.5)

    resp = await auth_client.post(js["items"][1]["url"], json={"runtime": 3},
                                  headers={"Accept": "text/plain"})
    assert resp.status_code == 200
    fid = resp.json()["fid"]
    await _wait(fid)

    resp = await auth_client.post(js["items"][1]["url"], json={"runtime": 3})
    assert resp.status_code == 200
    resp.headers["Content-Type"] == "text/event-stream"

    assert js["items"][2]["method"] == "POST"
    resp = await auth_client.post(js["items"][2]["url"], json={"runtime": 3},
                                  headers={"Accept": "text/plain"})
    assert resp.status_code == 200
    fid = resp.json()["fid"]
    await _wait(fid)

    serve.shutdown()
