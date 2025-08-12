import pytest
import json
from tests.test_execution import (register_flow, wait_for_job,
                                  app_server3, spooler_server, koco_server)
from kodosumi.runner.files import AsyncFileSystem
from pathlib import Path
import ray
from kodosumi.runner.files import SyncFileSystem
from kodosumi.core import Tracer, ServeAPI, Launch
from kodosumi.service.inputs.forms import Model, InputText, Checkbox, InputFiles, Submit, Cancel
from fastapi import Request
import pickle
from tempfile import mkdtemp
import asyncio
from tests.test_ray import env

@pytest.mark.asyncio
async def test_simple(app_server3, spooler_server, koco_server):
    client, _ = await register_flow(app_server3, koco_server)

    files_data = [
        ("docs/document1.txt", b"This is the first document content. " * 30),
        ("docs/document2.txt", b"This is the second document content. " * 50),
        ("image_data.bin", b"BINARY_IMAGE_DATA_" * 10 * 1024 * 1024)
    ]
    batch_response = await client.post(f"{koco_server}/files/init_batch")
    assert batch_response.status_code == 201
    batch_id = batch_response.json()["batch_id"]
    upload_ids = []
    chunk_size = 1 * 1024 * 1024  # 5MB chunks (same as frontend)
    for filename, file_data in files_data:
        total_chunks = (len(file_data) + chunk_size - 1) // chunk_size  
        init_payload = {
            "filename": filename,
            "total_chunks": total_chunks,
            "batch_id": batch_id
        }
        init_response = await client.post(
            f"{koco_server}/files/init", json=init_payload)
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
            response = await client.post(f"{koco_server}/files/chunk", 
                                         data=form_data, files=files)
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
    form_data = {
        "name": "hello world",
        "files": json.dumps(complete_payload)
    }
    resp = await client.post(f"{koco_server}/-/localhost/8125/-/simple",
                             json=form_data, timeout=300)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    assert fid is not None

    status = await wait_for_job(client, koco_server, fid)
    assert status == "finished"

    fs = AsyncFileSystem(fid, koco_server, client.cookies["kodosumi_jwt"])
    listing = await fs.ls()
    assert [f["path"] for f in listing] == [
        "in/docs/document1.txt", 
        "in/docs/document2.txt", 
        "in/image_data.bin"]
    total = 0
    async with fs.open("in/image_data.bin") as f:
        async for chunk in f.read():
            total += len(chunk)
    assert total == len(b"BINARY_IMAGE_DATA_" * 10 * 1024 * 1024)
    fh = fs.open("in/docs")
    fh.close()
    fh = fs.open("in/image_data.bin")
    total = 0
    async for chunk in fh.read():
        total += len(chunk)
    await fh.close()
    assert total == len(b"BINARY_IMAGE_DATA_" * 10 * 1024 * 1024)
    fh = fs.open("in/image_data.bin")
    chunk = await fh.read_all()
    assert chunk == b"BINARY_IMAGE_DATA_" * 10 * 1024 * 1024
    success = await fh.remove()
    assert success
    await fh.close()
    fh = fs.open("in/image_data.bin")
    with pytest.raises(FileNotFoundError):
        await fh.read_all()
    await fh.close()
    with pytest.raises(FileNotFoundError):
        await fs.remove("in/image_data.bin")
    success = await fs.remove("in/docs")
    assert success
    await fs.close()


@ray.remote
def process_file1(file: str, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug(63255)
    fs = tracer.fs_sync()
    for file in fs.download("in/"):
        pass
    return file

async def runner1(inputs: dict, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug()
    tmp_dir = mkdtemp()

    fs = await tracer.fs()
    ret = await fs.ls("in")
    pickle.dump(ret, open(f"{tmp_dir}/output.dat", "wb"))

    ret = []
    futures = []
    async for file in fs.download("in/"):
        ret.append(file)
        futures.append(process_file1.remote(file, tracer))
    result = await asyncio.gather(*futures)
    pickle.dump(ret, open(f"{tmp_dir}/download.dat", "wb"))

    await fs.upload(tmp_dir)
    await fs.close()

    return result

    return {"result": "success"}

async def runner0(inputs: dict, tracer: Tracer):
    # from kodosumi.helper import debug
    # debug()
    return {"result": "success"}


# def app_factory1():
#     app = ServeAPI()
#     form_model = Model(
#         InputText(label="Runner", name="runner"),
#         Checkbox(label="Error", name="throw", value=False),
#         InputFiles(label="Upload Files", name="files", multiple=True, 
#                    directory=False),
#         Submit("Submit"),
#         Cancel("Cancel"),
#     )

#     @app.enter(
#         "/runner",
#         model=form_model,
#         summary="Test Flow",
#         deprecated=False,
#         description="Test Upload/Download Factory",
#     )
#     async def form1(inputs: dict, request: Request) -> dict:
#         runner = inputs.get("runner")
#         throw = inputs.get("throw")
#         if throw:
#             raise Exception("test error")
#         return Launch(request, runner, inputs=inputs)

#     return app


@pytest.mark.asyncio
async def test_upload_download(env, tmp_path):
    await env.start_app("tests.test_ray:app_factory1")
    files_data = [
        ("inputs.txt", "this is a test file"),
        ("data/file1.dat", "1000"),
        ("data/file2.dat", "2000"),
        ("data/file3.dat", "3000"),
        ("data/file4.dat", "4000"),
    ]
    files_payload = await env.upload_files(files_data)
    form_data = {
        "runner": "tests.test_upload:runner1",
        "throw": "off",
        "files": json.dumps(files_payload)
    }
    resp = await env.post("/-/localhost/8125/-/runner", json=form_data)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    status = await env.wait_for(fid, "finished", "error")
    assert status == "finished"

    fs = SyncFileSystem(
        fid, env.panel_url, env.client.cookies.get("kodosumi_jwt"))
    ret = fs.ls("in")
    assert [f["path"] for f in ret] == sorted([
        "in/inputs.txt",
        "in/data/file1.dat",
        "in/data/file2.dat",
        "in/data/file3.dat",
        "in/data/file4.dat",
    ])
    ret = fs.ls("out")
    out_files = [f["path"] for f in ret]
    assert len(out_files) >= 1
    assert "out/output.dat" in out_files
    assert len(out_files) >= 2
    assert "out/download.dat" in out_files
    print("OK")


def _norm(filename):
    p = Path(filename)
    idx = [i for i, k in enumerate(p.parts) if k.startswith("kodosumi-")][0]
    return "/".join(p.parts[idx+1:])


@pytest.mark.asyncio
async def test_filesystem(env, tmp_path):
    await env.start_app("tests.test_ray:app_factory1")
    files_data = [
        ("inputs.txt", "this is a test file"),
        ("data/file1.dat", "1000"),
        ("data/file2.dat", "2000"),
        ("data/file3.dat", "3000"),
        ("data/file4.dat", "4000"),
        ("data/folder/file5.dat", "5000"),
        ("data/folder/file6.dat", "6000"),
        ("data/folder/file7.dat", "7000"),
        ("data/folder/file8.dat", "8000"),
    ]
    files_payload = await env.upload_files(files_data)
    form_data = {
        "runner": "tests.test_upload:runner0",
        "throw": "off",
        "files": json.dumps(files_payload)
    }
    resp = await env.post("/-/localhost/8125/-/runner", json=form_data)
    assert resp.status_code == 200
    fid = resp.json()["result"]
    status = await env.wait_for(fid, "finished", "error")
    assert status == "finished"

    fs = SyncFileSystem(
        fid, env.panel_url, env.client.cookies.get("kodosumi_jwt"))
    afs = AsyncFileSystem(
        fid, env.panel_url, env.client.cookies.get("kodosumi_jwt"))
    expected_in = sorted([
        "in/inputs.txt",
        "in/data/file1.dat", 
        "in/data/file2.dat", 
        "in/data/file3.dat", 
        "in/data/file4.dat", 
        "in/data/folder/file5.dat",
        "in/data/folder/file6.dat",
        "in/data/folder/file7.dat",
        "in/data/folder/file8.dat"
    ])
    ret = fs.ls("in")
    assert [f["path"] for f in ret] == expected_in
    ret = await afs.ls("in")
    assert [f["path"] for f in ret] == expected_in

    ret = fs.ls("/in")
    assert [f["path"] for f in ret] == expected_in
    ret = await afs.ls("/in")
    assert [f["path"] for f in ret] == expected_in

    ret = fs.ls("/in/")
    assert [f["path"] for f in ret] == expected_in
    ret = await afs.ls("/in/")
    assert [f["path"] for f in ret] == expected_in

    ret = fs.ls("in/")
    assert [f["path"] for f in ret] == expected_in
    ret = await afs.ls("in/")
    assert [f["path"] for f in ret] == expected_in

    with pytest.raises(FileNotFoundError):
        fs.ls("in/data")
    with pytest.raises(FileNotFoundError):
        await afs.ls("in/data")

    upload_data = [
        ("outputs.txt", "this is a test file"),
        ("data/outputs1.dat", "1000"),
        ("data/outputs2.dat", "2000"),
        ("data/outputs3.dat", "3000"),
        ("data/outputs4.dat", "4000"),
        ("data/folder/outputs5.dat", "5000"),
        ("data/folder/outputs6.dat", "6000"),
        ("data/folder/outputs7.dat", "7000"),
        ("data/folder/outputs8.dat", "8000"),
    ]
    
    upload_dir = tmp_path / "upload"
    upload_dir.mkdir()
    for filename, content in upload_data:
        path = upload_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    fs.upload(str(upload_dir))
    ret = fs.ls("out")

    upload_data2 = [
        ("outputs2.txt", "this is a test file"),
        ("data2/outputs1.dat", "1000"),
        ("data2/outputs2.dat", "2000"),
        ("data2/outputs3.dat", "3000"),
        ("data2/outputs4.dat", "4000"),
        ("data2/folder/outputs5.dat", "5000"),
        ("data2/folder/outputs6.dat", "6000"),
        ("data2/folder/outputs7.dat", "7000"),
        ("data2/folder/outputs8.dat", "8000"),
    ]
    
    upload_dir = tmp_path / "upload2"
    upload_dir.mkdir()
    for filename, content in upload_data2:
        path = upload_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    fs.upload(str(upload_dir))
    ret = fs.ls("out")

    upload_data3 = [
        ("outputs3.txt", "hello world"),
    ]
    
    upload_dir = tmp_path / "upload3"
    upload_dir.mkdir()
    for filename, content in upload_data3:
        path = upload_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    fs.upload(str(upload_dir / "outputs3.txt"))

    expected_out = sorted([
        "out/" + u[0] for u in upload_data + upload_data2 + upload_data3
    ]) 
    ret = fs.ls("out")
    assert expected_out == sorted([u["path"] for u in ret])
    ret = await afs.ls("out")
    assert expected_out == sorted([u["path"] for u in ret])

    ret = fs.ls("in")
    assert expected_in == sorted([u["path"] for u in ret])
    ret = await afs.ls("in")
    assert expected_in == sorted([u["path"] for u in ret])

    ret = list(fs.download("out/"))
    ret = [_norm(f) for f in ret]
    assert ret == ["/".join(e.split("/")[1:]) for e in expected_out]
    ret = []
    async for file in afs.download("out/"):
        ret.append(file)
    ret = [_norm(f) for f in ret]
    assert ret == ["/".join(e.split("/")[1:]) for e in expected_out]

    expected = [
        "outputs5.dat",
        "outputs6.dat",
        "outputs7.dat",
        "outputs8.dat",
    ]
    ret = list(fs.download("out/data/folder"))
    assert [_norm(f) for f in ret] == expected
    ret = []
    async for file in afs.download("out/data/folder"):
        ret.append(file)
    assert [_norm(f) for f in ret] == expected

    ret = list(fs.download("out/data/folder/outputs5.dat"))
    assert len(ret) == 1
    assert Path(ret[0]).name == "outputs5.dat"
    ret = []
    async for file in afs.download("out/data/folder/outputs5.dat"):
        ret.append(file)
    assert len(ret) == 1
    assert Path(ret[0]).name == "outputs5.dat"

    with pytest.raises(FileNotFoundError):
        list(fs.download("out/data/folder/outputs5.NOT-FOUND"))
    with pytest.raises(FileNotFoundError):
        async for file in afs.download("out/data/folder/outputs5.NOT-FOUND"):
            print(file)

    with pytest.raises(FileNotFoundError):
        list(fs.download("out/data/folder-NOT-FOUND"))
    with pytest.raises(FileNotFoundError):
        async for file in afs.download("out/data/folder-NOT-FOUND"):
            print(file)
    fs.remove("out/data/folder/outputs5.dat")
    fs.remove("out/data/folder/outputs6.dat")
    await afs.remove("out/data/folder/outputs7.dat")

    expected = [
        "outputs8.dat",
    ]
    ret = list(fs.download("out/data/folder"))
    assert [_norm(f) for f in ret] == expected
    ret = []
    async for file in afs.download("out/data/folder"):
        ret.append(file)
    assert [_norm(f) for f in ret] == expected
 
    fs.remove("out/data/folder/outputs8.dat")
    with pytest.raises(FileNotFoundError):
        list(fs.download("out/data/folder"))
    with pytest.raises(FileNotFoundError):
        async for file in afs.download("out/data/folder"):
            print(file)

    expected = sorted([
        "outputs1.dat",
        "outputs2.dat",
        "outputs3.dat",
        "outputs4.dat",
        "folder/outputs5.dat",
        "folder/outputs6.dat",
        "folder/outputs7.dat",
        "folder/outputs8.dat",
    ])
    ret = list(fs.download("out/data2"))
    assert [_norm(f) for f in ret] == expected 
    ret = []
    async for file in afs.download("out/data2"):
        ret.append(file)
    assert [_norm(f) for f in ret] == expected

    await afs.remove("out/data2")
    with pytest.raises(FileNotFoundError):
        list(fs.download("out/data2/folder"))
    with pytest.raises(FileNotFoundError):
        async for file in afs.download("out/data2/folder"):
            print(file)

    with pytest.raises(FileNotFoundError):
        list(fs.download("out/data2"))
    with pytest.raises(FileNotFoundError):
        async for file in afs.download("out/data2"):
            print(file)

    await afs.close()
    fs.close()