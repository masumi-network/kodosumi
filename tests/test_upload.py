import pytest
import json
from tests.test_execution import (register_flow, wait_for_job,
                                  app_server3, spooler_server, koco_server)
from kodosumi.runner.files import AsyncFileSystem


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
    chunk_size = 5 * 1024 * 1024  # 5MB chunks (same as frontend)
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
