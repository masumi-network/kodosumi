import asyncio
from enum import Enum, auto
from pathlib import Path
from tempfile import mkdtemp
from typing import List, Literal, Union

from httpx import AsyncClient, Client, Response

from kodosumi.config import InternalSettings

class StreamState(Enum):
    FRESH = auto()
    OPEN = auto()
    CLOSED = auto()


DirectoryType = Literal["in", "out"]


class AsyncFileStream:

    def __init__(self, fs: "AsyncFileSystem", path: str, stream_context):
        self._fs = fs
        self._path = path
        self._stream_context = stream_context
        self._response: Response | None = None
        self._iterator = None
        self._state = StreamState.FRESH

    async def _open(self):
        if self._state == StreamState.OPEN:
            return
        if self._state == StreamState.CLOSED:
            raise RuntimeError("Cannot operate on a closed stream.")
        self._response = await self._stream_context.__aenter__()
        if self._response.status_code == 404:
            raise FileNotFoundError(self._path)
        self._response.raise_for_status()
        self._iterator = self._response.aiter_bytes()
        self._state = StreamState.OPEN

    async def read(self):
        await self._open()        
        while True:
            try:
                chunk = await self._iterator.__anext__()
                yield chunk
            except StopAsyncIteration:
                break

    async def read_all(self) -> bytes:
        try:
            chunks = [chunk async for chunk in self.read()]
            return b"".join(chunks)
        except Exception as e:
            raise FileNotFoundError(self._path) from e

    async def remove(self) -> bool:
        await self.close()
        return await self._fs.remove(self._path)

    async def close(self):
        await self.__aexit__(None, None, None)

    async def __aenter__(self):
        if self._state != StreamState.FRESH:
            raise RuntimeError("Stream context is not re-entrant.")
        await self._open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._state == StreamState.OPEN:
            self._state = StreamState.CLOSED
            await self._stream_context.__aexit__(exc_type, exc_val, exc_tb)


class SyncFileStream:

    def __init__(self, fs: "SyncFileSystem", path: str, stream_context):
        self._fs = fs
        self._path = path
        self._stream_context = stream_context
        self._response: Response | None = None
        self._iterator = None
        self._state = StreamState.FRESH

    def _open(self):
        if self._state == StreamState.OPEN:
            return
        if self._state == StreamState.CLOSED:
            raise RuntimeError("Cannot operate on a closed stream.")
        
        self._response = self._stream_context.__enter__()
        if self._response.status_code == 404:
            raise FileNotFoundError(self._path)
        self._response.raise_for_status()
        self._iterator = self._response.iter_bytes()
        self._state = StreamState.OPEN

    def read(self):
        self._open()
        while True:
            try:
                chunk = self._iterator.__next__()
                yield chunk
            except StopIteration:
                break

    def read_all(self) -> bytes:
        try:
            chunks = [chunk for chunk in self.read()]
            return b"".join(chunks)
        except Exception as e:
            raise FileNotFoundError(self._path) from e

    def remove(self) -> bool:
        self.close()
        return self._fs.remove(self._path)

    def close(self):
        self.__exit__(None, None, None)

    def __enter__(self):
        if self._state != StreamState.FRESH:
            raise RuntimeError("Stream context is not re-entrant.")
        self._open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._state == StreamState.OPEN:
            self._state = StreamState.CLOSED
            self._stream_context.__exit__(exc_type, exc_val, exc_tb)


class AsyncFileSystem:
    def __init__(self, fid: str, panel_url: str, jwt: str):
        self.fid = fid
        self.panel_url = panel_url
        self.jwt = jwt
        self._client = AsyncClient(
            timeout=300, cookies={"kodosumi_jwt": self.jwt},
            base_url=self.panel_url)
        settings = InternalSettings()
        self.chunk_size = settings.CHUNK_SIZE

    async def ls(self, root: DirectoryType = "in") -> List[dict]:
        resp = await self._client.get(f"/files/{self.fid}/{root}")
        if resp.status_code == 404:
            raise FileNotFoundError(root)
        resp.raise_for_status()
        return resp.json()

    def open(self, 
             path: str, 
             root: DirectoryType = "in") -> "AsyncFileStream":
        stream_context = self._client.stream(
            "GET", f"/files/{self.fid}/{root}/{path.lstrip("/")}")
        return AsyncFileStream(self, path, stream_context)

    async def remove(self, 
                     path: str, 
                     root: DirectoryType = "in") -> bool:
        resp = await self._client.delete(
            f"/files/{self.fid}/{root}/{path.lstrip("/")}")
        if resp.status_code != 204:
            raise FileNotFoundError(path)
        return True
    
    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def download(self, root: DirectoryType = "in") :
        resp = await self._client.get(f"/files/{self.fid}/{root}")
        if resp.status_code == 404:
            raise FileNotFoundError(root)
        resp.raise_for_status()
        folder = mkdtemp(prefix="kodosumi-")
        for file in resp.json():
            if file["is_directory"]:
                continue
            target = Path(folder).joinpath(file["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            async with self.open(file["path"], root=root) as src:
                with open(str(target), "wb") as dst:
                    async for chunk in src.read():
                        dst.write(chunk)
                    yield str(target)

    async def upload(self, path: Union[str, Path]):
        batch_response = await self._client.post("/files/init_batch")
        assert batch_response.status_code == 201
        batch_id = batch_response.json()["batch_id"]
        upload_ids = []
        chunk_size = self.chunk_size
        for file in Path(path).rglob("*"):
            if file.is_dir():
                continue
            total_chunks = (
                file.stat().st_size + chunk_size - 1) // chunk_size  
            init_payload = {
                "filename": str(file.relative_to(Path(path))),
                "total_chunks": total_chunks,
                "batch_id": batch_id
            }
            init_response = await self._client.post(
                "/files/init", json=init_payload)
            assert init_response.status_code == 201
            upload_data = init_response.json()
            upload_ids.append({
                "upload_id": upload_data["upload_id"],
                "total_chunks": total_chunks,
                "file": file,
                "size": file.stat().st_size
            })
        items = {}
        for upload_info in upload_ids:
            upload_id = upload_info["upload_id"]
            file = upload_info["file"]
            total_chunks = upload_info["total_chunks"]
            with file.open("rb") as fh:
                for chunk_num in range(total_chunks):
                    chunk_data = fh.read(chunk_size)
                    if not chunk_data:
                        break
                    form_data = {
                        "upload_id": upload_id,
                        "chunk_number": str(chunk_num),
                    }
                    files = {
                        "chunk": (f"chunk_{chunk_num}",
                                chunk_data,
                                "application/octet-stream")
                    }
                    response = await self._client.post(
                        "/files/chunk", data=form_data, files=files)
                    assert response.status_code == 201
                    data = response.json()
                    assert data["status"] == "chunk received"
                    assert data["chunk_number"] == chunk_num
                    await asyncio.sleep(0)
            items[upload_id] = {
                "filename": str(file.relative_to(Path(path))),
                "totalChunks": total_chunks
            }
        response = await self._client.post(
            f"/files/complete/{self.fid}/{batch_id}/out", json=items)
        assert response.status_code == 201
        return batch_id
    
class SyncFileSystem:
    def __init__(self, fid: str, panel_url: str, jwt: str):
        self.fid = fid
        self.panel_url = panel_url
        self.jwt = jwt
        self._client = Client(
            timeout=300, cookies={"kodosumi_jwt": self.jwt},
            base_url=self.panel_url)
        settings = InternalSettings()
        self.chunk_size = settings.CHUNK_SIZE

    def ls(self, root: DirectoryType = "in") -> List[dict]:
        resp = self._client.get(f"/files/{self.fid}/{root}")
        if resp.status_code == 404:
            raise FileNotFoundError(root)
        resp.raise_for_status()
        return resp.json()

    def open(self, 
             path: str, 
             root: DirectoryType = "in") -> "SyncFileStream":
        stream_context = self._client.stream(
            "GET", f"/files/{self.fid}/{root}/{path.lstrip("/")}")
        return SyncFileStream(self, path, stream_context)

    def remove(self, 
               path: str, 
               root: DirectoryType = "in") -> bool:
        resp = self._client.delete(
            f"/files/{self.fid}/{root}/{path.lstrip("/")}")
        if resp.status_code != 204:
            raise FileNotFoundError(path)
        return True
    
    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def download(self, root: DirectoryType = "in"):
        resp = self._client.get(f"/files/{self.fid}/{root}")
        if resp.status_code == 404:
            raise FileNotFoundError(root)
        resp.raise_for_status()
        folder = mkdtemp(prefix="kodosumi-")
        for file in resp.json():
            if file["is_directory"]:
                continue
            target = Path(folder).joinpath(file["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with self.open(file["path"], root=root) as src:
                with open(str(target), "wb") as dst:
                    for chunk in src.read():
                        dst.write(chunk)
                    yield str(target)

    def upload(self, path: Union[str, Path]):
        batch_response = self._client.post("/files/init_batch")
        assert batch_response.status_code == 201
        batch_id = batch_response.json()["batch_id"]
        upload_ids = []
        chunk_size = self.chunk_size
        for file in Path(path).rglob("*"):
            if file.is_dir():
                continue
            total_chunks = (
                file.stat().st_size + chunk_size - 1) // chunk_size  
            init_payload = {
                "filename": str(file.relative_to(Path(path))),
                "total_chunks": total_chunks,
                "batch_id": batch_id
            }
            init_response = self._client.post("/files/init", json=init_payload)
            assert init_response.status_code == 201
            upload_data = init_response.json()
            upload_ids.append({
                "upload_id": upload_data["upload_id"],
                "total_chunks": total_chunks,
                "file": file,
                "size": file.stat().st_size
            })
        items = {}
        for upload_info in upload_ids:
            upload_id = upload_info["upload_id"]
            file = upload_info["file"]
            total_chunks = upload_info["total_chunks"]
            with file.open("rb") as fh:
                for chunk_num in range(total_chunks):
                    chunk_data = fh.read(chunk_size)
                    if not chunk_data:
                        break
                    form_data = {
                        "upload_id": upload_id,
                        "chunk_number": str(chunk_num),
                    }
                    files = {
                        "chunk": (f"chunk_{chunk_num}",
                                chunk_data,
                                "application/octet-stream")
                    }
                    response = self._client.post(
                        "/files/chunk", data=form_data, files=files)
                    assert response.status_code == 201
                    data = response.json()
                    assert data["status"] == "chunk received"
                    assert data["chunk_number"] == chunk_num
            items[upload_id] = {
                "filename": str(file.relative_to(Path(path))),
                "totalChunks": total_chunks
            }
        response = self._client.post(
            f"/files/complete/{self.fid}/{batch_id}/out", json=items)
        assert response.status_code == 201
        return batch_id
