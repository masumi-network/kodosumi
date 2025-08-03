import asyncio
import os
import re
import json
import time
from multiprocessing import Process
from pathlib import Path
from random import random
from kodosumi.runner.files import AsyncFileSystem, SyncFileSystem

import pytest
import ray
from fastapi import Request
from httpx import AsyncClient

import kodosumi.service.app
import kodosumi.service.server
import kodosumi.spooler
from kodosumi.config import InternalSettings
from kodosumi.core import Launch, ServeAPI, Tracer
from kodosumi.service.inputs.forms import (
    Cancel, Checkbox, InputText, Model, Submit, InputFiles)


@ray.remote # (num_cpus=4)
def process_range1(num: int, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug()
    tracer.debug_sync(f"process {num}")
    result = []
    for i in range(100):
        r = random()
        result.append(r)
        tracer.debug_sync(f"process {num}: {r}")
    tracer.debug_sync(f"process {num}: done")
    fs = tracer.fs_sync()
    fh = fs.open("docs/document1.txt")
    for chunk in fh.read():
        tracer.debug_sync(f"got {num} line: {chunk}")
    tracer.debug_sync(f"process {num}: done")
    fh.close()
    fs.close()
    return result


async def runner1(inputs: dict, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug()
    futures = [process_range1.remote(i, tracer) for i in range(10)]
    result = await asyncio.gather(*futures)
    return result


@ray.remote # (num_cpus=4)
def process_range2(num: int, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug()
    tracer.debug_sync(f"process {num}")
    fs = tracer.fs_sync()
    fh = fs.open("docs/document1.txt")
    result = []
    for chunk in fh.read():
        result.append(len(chunk))
    fh.close()
    fs.close()
    return {"result": result}


async def runner2(inputs: dict, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug()
    fs = tracer.fs()
    futures = [process_range2.remote(i, tracer) for i in range(10)]
    result = await asyncio.gather(*futures)
    return result


async def runner3(inputs: dict, tracer: Tracer):
    return {"ok": True}


def app_factory1():
    app = ServeAPI()
    form_model = Model(
        InputText(label="Runner", name="runner"),
        Checkbox(label="Error", name="throw", value=False),
        InputFiles(label="Upload Files", name="files", multiple=True, 
                   directory=False),
        Submit("Submit"),
        Cancel("Cancel"),
    )

    @app.enter(
        "/runner",
        model=form_model,
        summary="Factory 1",
        deprecated=False,
        description="launches arbitrary runner",
    )
    async def form1(inputs: dict, request: Request) -> dict:
        runner = inputs.get("runner")
        throw = inputs.get("throw")
        if throw:
            raise Exception("test error")
        return Launch(request, f"tests.test_ray:{runner}", inputs=inputs)

    return app


def run_uvicorn(factory: str, port: int):
    import uvicorn
    uvicorn.run(
        factory,
        host="localhost",
        port=port,
        reload=False
    )


class Environment:

    def __init__(self, tmp_path):
        self.spooler = None
        self.panel = None
        self._port = 8125
        self.apps = {}
        self.panel_url = "http://localhost:8120"
        self.tmp_path = tmp_path / "kodosumi-test"
        self.tmp_path = Path("/tmp") / "kodosumi-test"
        if not self.tmp_path.exists():
            self.tmp_path.mkdir()
        self.client = AsyncClient(timeout=300, base_url=self.panel_url)

    def __getattr__(self, name):
        if name in ("get", "post", "put", "delete"):
            return getattr(self.client, name)


    async def startup(self):
        os.environ["KODO_EXEC_DIR"] = f"{self.tmp_path}/data/execution"
        os.environ["KODO_SPOOLER_LOG_FILE"] = f"{self.tmp_path}/data/spooler.log"
        os.environ["KODO_UPLOAD_DIR"] = f"{self.tmp_path}/data/uploads"
        os.environ["KODO_APP_LOG_FILE"] = f"{self.tmp_path}/data/app.log"
        os.environ["KODO_APP_SERVER"] = self.panel_url
        os.environ["KODO_ADMIN_DATABASE"] = f"sqlite+aiosqlite:///{self.tmp_path}/data/admin.db"
        self.spooler = Process(target=kodosumi.spooler.run)
        self.spooler.start()
        self.panel = Process(
            target=kodosumi.service.server.run, args=(InternalSettings(),))
        self.panel.start()
        end = time.time() + 10
        while True:
            try:
                resp = await self.get("/login?name=admin&password=admin")
                if resp.status_code == 200:
                    break
            except Exception:
                pass
            if time.time() > end:
                raise Exception("Panel not ready")
            await asyncio.sleep(0.25)

    def shutdown(self):
        apps = [a["process"] for a in self.apps.values()]
        for proc in [self.spooler, self.panel] + apps:
            proc.kill()
            proc.join()

    async def start_app(self, factory):
        port = self._port
        proc = Process(target=run_uvicorn, args=(factory, port,))
        proc.start()
        self._port += 1
        app_url = f"http://localhost:{port}"
        self.apps[factory] = {
            'process': proc,
            'url': app_url,
            'endpoints': None
        }
        end = time.time() + 10
        while True:
            resp = await self.post(
                "/flow/register",
                json={"url": [f"{app_url}/openapi.json"]})
            if resp.status_code == 201:
                break
            await asyncio.sleep(0.25)
            if time.time() > end:
                raise Exception("Panel not ready")
        self.apps[factory]["endpoints"] = resp.json()

    async def upload_files(self, files_data: list) -> dict:
        batch_response = await self.client.post(f"/files/init_batch")
        assert batch_response.status_code == 201
        batch_id = batch_response.json()["batch_id"]
        upload_ids = []
        chunk_size = 5 * 1024 * 1024  # 5MB chunks (same as frontend)
        for filename, file_data in files_data:
            total_chunks = (len(file_data) + chunk_size - 1) // chunk_size
            init_payload = {
                "filename": filename,
                "total_chunks": total_chunks,
                "batch_id": batch_id
            }
            init_response = await self.client.post(f"/files/init", 
                                                   json=init_payload)
            assert init_response.status_code == 201
            upload_data = init_response.json()
            upload_ids.append({
                "upload_id": upload_data["upload_id"],
                "total_chunks": total_chunks,
                "filename": filename
            })
        for i, (filename, file_data) in enumerate(files_data):
            upload_id = upload_ids[i]["upload_id"]
            total_chunks = upload_ids[i]["total_chunks"]
            for chunk_num in range(total_chunks):
                start_byte = chunk_num * chunk_size
                end_byte = min(start_byte + chunk_size, len(file_data))
                chunk_data = file_data[start_byte:end_byte]
                form_data = {
                    "upload_id": upload_id,
                    "chunk_number": str(chunk_num),
                }
                files = {
                    "chunk": (f"chunk_{chunk_num}",
                              chunk_data,
                              "application/octet-stream")
                }
                response = await self.client.post(
                    f"/files/chunk", data=form_data, files=files)
                assert response.status_code == 201
                data = response.json()
                assert data["status"] == "chunk received"
                assert data["chunk_number"] == chunk_num

        complete_payload = {
            "batchId": batch_id,
            "name": "files",
            "items": {}
        }
        for upload_id in upload_ids:
            complete_payload["items"][upload_id["upload_id"]] = {
                "filename": upload_id["filename"],
                "totalChunks": upload_id["total_chunks"]
            }
        return complete_payload


    async def wait_for(self, fid, *statuses):
        while True:
            resp = await self.get(f"/outputs/status/{fid}")
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status in statuses:
                    return status
            await asyncio.sleep(0.25)


@pytest.fixture
async def env(tmp_path):
    env = Environment(tmp_path)
    await env.startup()
    yield env
    env.shutdown()


@pytest.mark.asyncio
async def test_environment(env):
    await env.start_app("tests.test_ray:app_factory1")
    resp = await env.get("/inputs/-/localhost/8125/runner")
    assert resp.status_code == 200

    files_data = [
        ("docs/document1.txt", b"This is the first document content. " * 30),
        ("docs/document2.txt", b"This is the second document content. " * 50),
    ]
    files_payload = await env.upload_files(files_data)

    form_data = {
        "runner": "runner1",
        "throw": "off",
        "files": json.dumps(files_payload)
    }

    resp = await env.post("/-/localhost/8125/-/runner", json=form_data)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    status = await env.wait_for(fid, "finished", "error")
    assert status == "finished"
    found = set()
    async with env.client.stream('GET', f"/outputs/stdio/{fid}", timeout=120) as resp:
        async for line in resp.aiter_lines():
            match = re.match(r".+got (\d+) line", line)
            if match:
                found.add(match.group(1))
    assert sorted(found) == ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']


def _norm(filename):
    p = Path(filename)
    idx = [i for i, k in enumerate(p.parts) if k.startswith("kodosumi-")][0]
    return "/".join(p.parts[idx+1:])


@pytest.mark.asyncio
async def test_download(env):
    await env.start_app("tests.test_ray:app_factory1")
    files_data = [
        ("docs/document1.txt", b"This is the first document content. "),
        ("docs/document2.txt", b"This is the second document content. "),
        ("docs/document3.txt", b"This is the third document content. "),
        ("docs/assets/asset1.txt", b"This is the first asst content. "),
        ("docs/assets/asset2.txt", b"This is the second asst content. "),
        ("text1.txt", b"This is the first text content. "),
        ("text2.txt", b"This is the second text content. "),
        ("text3.txt", b"This is the third text content. ")
    ]
    files_payload = await env.upload_files(files_data)
    form_data = {
        "runner": "runner2",
        "throw": "off",
        "files": json.dumps(files_payload)
    }
    resp = await env.post("/-/localhost/8125/-/runner", json=form_data)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    status = await env.wait_for(fid, "finished", "error")
    assert status == "finished"

    expected = sorted([
        "docs",
        "docs/document1.txt",
        "docs/document2.txt",
        "docs/document3.txt",
        "docs/assets",
        "docs/assets/asset1.txt",
        "docs/assets/asset2.txt",
        "text1.txt",
        "text2.txt",
        "text3.txt",
    ])

    fs = SyncFileSystem(
        fid, env.panel_url, env.client.cookies.get("kodosumi_jwt"))
    afs = AsyncFileSystem(
        fid, env.panel_url, env.client.cookies.get("kodosumi_jwt"))

    ret = fs.ls("in")
    assert [f["path"] for f in ret] == expected

    ret = await afs.ls("in")
    assert [f["path"] for f in ret] == expected

    ret = fs.open("docs/document1.txt", "in")
    assert ret.read_all() == b"This is the first document content. "
    ret.close()
    ret.close()
    ret.remove()

    ret = afs.open("docs/document2.txt")
    assert await ret.read_all() == b"This is the second document content. "
    await ret.close()

    ret = fs.open("docs/document1.txt")
    with pytest.raises(FileNotFoundError):
        ret.read_all()
    
    ret = afs.open("docs/document1.txt")
    with pytest.raises(FileNotFoundError):
        await ret.read_all()

    fs.remove("text1.txt")
    ret = fs.open("text1.txt")
    with pytest.raises(FileNotFoundError):
        for chunk in ret.read():
            print(chunk)

    ret = afs.open("text1.txt")
    with pytest.raises(FileNotFoundError):
        await ret.read_all()

    ret = afs.open("text2.txt")
    async for chunk in ret.read():
        print(chunk)

    ret = afs.open("text1.txt", "in")
    with pytest.raises(FileNotFoundError):
        await ret.remove()

    expected = sorted([
        "docs",
        "docs/document2.txt",
        "docs/document3.txt",
        "docs/assets",
        "docs/assets/asset1.txt",
        "docs/assets/asset2.txt",
        "text2.txt",
        "text3.txt",
    ])

    ret = await afs.ls("in")
    assert [f["path"] for f in ret] == expected

    expected = sorted([
        "docs/document2.txt",
        "docs/document3.txt",
        "docs/assets/asset1.txt",
        "docs/assets/asset2.txt",
        "text2.txt",
        "text3.txt",
    ])
    listing = list(fs.download())
    ret = [_norm(f) for f in listing]
    assert ret == expected
    assert all([Path(p).exists() for p in listing])

    listing = []
    async for f in afs.download():
        listing.append(f)
    ret = [_norm(f) for f in listing]
    assert ret == expected
    assert all([Path(p).exists() for p in listing])

    fs.close()
    await afs.close()


@pytest.mark.asyncio
async def test_upload(env, tmp_path):
    await env.start_app("tests.test_ray:app_factory1")
    form_data = {
        "runner": "runner3",
        "throw": "off",
        "files": []
    }
    resp = await env.post("/-/localhost/8125/-/runner", json=form_data)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    status = await env.wait_for(fid, "finished", "error")
    assert status == "finished"

    fs = SyncFileSystem(
        fid, env.panel_url, env.client.cookies.get("kodosumi_jwt"))
    ret = fs.ls("in")
    assert ret == []
    ret = fs.ls("out")
    assert ret == []

    files_data = [
        ("docs/document1.txt", b"This is the first document content. "),
        ("docs/document2.txt", b"This is the second document content. "),
        ("docs/document3.txt", b"This is the third document content. "),
        ("text1.txt", b"This is the first text content. "),
        ("text2.txt", b"This is the second text content. "),
    ]
    upload_path = tmp_path / "upload-test"
    upload_path.mkdir(parents=True, exist_ok=True)
    for filename, content in files_data:
        target = upload_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as f:
            f.write(content)
    fs.upload(upload_path)
    ret = fs.ls("out")
    assert sorted([f["path"] for f in ret]) == sorted([
        "docs",
        "docs/document1.txt",
        "docs/document2.txt",
        "docs/document3.txt",
        "text1.txt",
        "text2.txt",
    ])
    listing = list(fs.download("out"))
    ret = [_norm(f) for f in listing]
    expected = sorted([
        "docs/document1.txt",
        "docs/document2.txt",
        "docs/document3.txt",
        "text1.txt",
        "text2.txt",
    ])
    assert ret == expected
    assert all([Path(p).exists() for p in listing])

    fs.remove("docs/document3.txt", "out")

    listing = list(fs.download("out"))
    ret = [_norm(f) for f in listing]
    expected = sorted([
        "docs/document1.txt",
        "docs/document2.txt",
        "text1.txt",
        "text2.txt",
    ])
    assert ret == expected
    assert all([Path(p).exists() for p in listing])

    fs.close()

@pytest.mark.asyncio
async def test_async_upload(env, tmp_path):
    await env.start_app("tests.test_ray:app_factory1")
    form_data = {
        "runner": "runner3",
        "throw": "off",
        "files": []
    }
    resp = await env.post("/-/localhost/8125/-/runner", json=form_data)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    status = await env.wait_for(fid, "finished", "error")
    assert status == "finished"

    fs = AsyncFileSystem(
        fid, env.panel_url, env.client.cookies.get("kodosumi_jwt"))
    ret = await fs.ls("in")
    assert ret == []
    ret = await fs.ls("out")
    assert ret == []

    files_data = [
        ("docs/document1.txt", b"This is the first document content. "),
        ("docs/document2.txt", b"This is the second document content. "),
        ("docs/document3.txt", b"This is the third document content. "),
        ("text1.txt", b"This is the first text content. "),
        ("text2.txt", b"This is the second text content. "),
    ]
    upload_path = tmp_path / "upload-test"
    upload_path.mkdir(parents=True, exist_ok=True)
    for filename, content in files_data:
        target = upload_path / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as f:
            f.write(content)
    await fs.upload(upload_path)
    ret = await fs.ls("out")
    assert sorted([f["path"] for f in ret]) == sorted([
        "docs",
        "docs/document1.txt",
        "docs/document2.txt",
        "docs/document3.txt",
        "text1.txt",
        "text2.txt",
    ])
    listing = []
    async for f in fs.download("out"):
        listing.append(f)
    ret = [_norm(f) for f in listing]
    expected = sorted([
        "docs/document1.txt",
        "docs/document2.txt",
        "docs/document3.txt",
        "text1.txt",
        "text2.txt",
    ])
    assert ret == expected
    assert all([Path(p).exists() for p in listing])

    await fs.remove("docs/document3.txt", "out")

    listing = []
    async for f in fs.download("out"):
        listing.append(f)
    ret = [_norm(f) for f in listing]
    expected = sorted([
        "docs/document1.txt",
        "docs/document2.txt",
        "text1.txt",
        "text2.txt",
    ])
    assert ret == expected
    assert all([Path(p).exists() for p in listing])

    fs.close()

