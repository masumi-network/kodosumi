import asyncio
from enum import Enum, auto
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Dict, List, Literal

from httpx import AsyncClient, Client, Response

from kodosumi.config import InternalSettings


class StreamState(Enum):
    FRESH = auto()
    OPEN = auto()
    CLOSED = auto()


DirectoryType = Literal["in", "out"]


def create_client_config(panel_url: str, jwt: str) -> Dict[str, Any]:
    return {
        "timeout": 300,
        "cookies": {"kodosumi_jwt": jwt},
        "base_url": panel_url,
        "follow_redirects": True
    }


def build_file_path(fid: str, path: str = "") -> tuple[str, str]:
    root, *sub_path = path.strip("/").split("/", 1)
    if root not in ("in", "out"):
        raise ValueError(f"invalid root '{root}'")
    if sub_path:
        file_path = "/".join(sub_path).rstrip("/")
        return f"/files/{fid}/{root}/{file_path}", f"{root}/{file_path}"
    return f"/files/{fid}/{root}", root


def validate_response(resp: Response, error_path: str) -> None:
    if resp.status_code == 404:
        raise FileNotFoundError(error_path)
    resp.raise_for_status()


def create_upload_payload(file: Path, 
                          base_path: Path, 
                          total_chunks: int, 
                          batch_id: str) -> Dict[str, Any]:
    return {
        "filename": str(file.relative_to(base_path)),
        "total_chunks": total_chunks,
        "batch_id": batch_id
    }


def create_chunk_payload(upload_id: str, 
                         chunk_num: int, 
                         chunk_data: bytes) -> tuple[
                             Dict[str, str], Dict[str, tuple]]:
    form_data = {
        "upload_id": upload_id,
        "chunk_number": str(chunk_num),
    }
    files = {
        "chunk": (f"chunk_{chunk_num}", chunk_data, "application/octet-stream")
    }
    return form_data, files


def create_completion_payload(file: Path, 
                              base_path: Path, 
                              total_chunks: int) -> Dict[str, Any]:
    return {
        "filename": str(file.relative_to(base_path)),
        "totalChunks": total_chunks
    }


def calculate_total_chunks(file_size: int, chunk_size: int) -> int:
    return (file_size + chunk_size - 1) // chunk_size


def get_files_to_upload(path: str) -> List[Path]:
    path_obj = Path(path)
    if path_obj.is_file():
        return [path_obj]
    if path_obj.is_dir():
        return [f for f in path_obj.rglob("*") if f.is_file()]
    return []


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
        config = create_client_config(panel_url, jwt)
        self._client = AsyncClient(**config)
        settings = InternalSettings()
        self.chunk_size = settings.CHUNK_SIZE

    async def ls(self, path: str = "in") -> List[dict]:
        api_path, sub_path = build_file_path(self.fid, path)
        resp = await self._client.get(api_path)
        validate_response(resp, sub_path)
        return resp.json()

    def open(self, path: str) -> "AsyncFileStream":
        api_path, sub_path = build_file_path(self.fid, path)
        stream_context = self._client.stream("GET", api_path)
        return AsyncFileStream(self, sub_path, stream_context)

    async def remove(self, path: str) -> bool:
        api_path, sub_path = build_file_path(self.fid, path)
        resp = await self._client.delete(api_path)
        if resp.status_code != 204:
            raise FileNotFoundError(sub_path)
        return True
    
    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def download(self, path: str = "in"):
        local_folder = mkdtemp(prefix="kodosumi-")
        root, *subpath = path.strip("/").split('/', 1)
        found = False
        search = Path(root) / "/".join(subpath)
        search_len = len(search.parts)
        for item in await self.ls(root):
            in_scope = False
            if not subpath:
                in_scope = True
                parent = Path(root)
                found = True
            else:
                item_path = Path(item["path"])
                if search == item_path:
                    in_scope = True
                    parent = search.parent
                    found = True
                elif search_len < len(item_path.parts):
                    if search.parts == item_path.parts[0:search_len]:
                        in_scope = True
                        parent = search
                        found = True
            if not in_scope:
                continue
            relative_path = Path(item['path']).relative_to(parent)
            target_path = Path(local_folder).joinpath(relative_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            async with self.open(item['path']) as src:
                with open(str(target_path), "wb") as dst:
                    async for chunk in src.read():
                        dst.write(chunk)
                yield str(target_path)
        if not found:
            raise FileNotFoundError(path)

    async def upload(self, path: str):
        batch_response = await self._client.post("/files/init_batch")
        assert batch_response.status_code == 201
        batch_id = batch_response.json()["batch_id"]
        
        path_obj = Path(path)
        base_path = path_obj.parent if path_obj.is_file() else path_obj
        files_to_upload = get_files_to_upload(path)
        if not files_to_upload:
            return None

        upload_ids = []
        
        for file in files_to_upload:
            total_chunks = calculate_total_chunks(
                file.stat().st_size, self.chunk_size)
            init_payload = create_upload_payload(
                file, base_path, total_chunks, batch_id)
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
                    chunk_data = fh.read(self.chunk_size)
                    if not chunk_data:
                        break
                    form_data, files = create_chunk_payload(
                        upload_id, chunk_num, chunk_data)
                    response = await self._client.post(
                        "/files/chunk", data=form_data, files=files)
                    assert response.status_code == 201
                    data = response.json()
                    assert data["status"] == "chunk received"
                    assert data["chunk_number"] == chunk_num
                    await asyncio.sleep(0)
            items[upload_id] = create_completion_payload(
                file, base_path, total_chunks)
        response = await self._client.post(
            f"/files/complete/{self.fid}/{batch_id}/out", json=items)
        assert response.status_code == 201
        return batch_id
    
class SyncFileSystem:
    def __init__(self, fid: str, panel_url: str, jwt: str):
        self.fid = fid
        self.panel_url = panel_url
        self.jwt = jwt
        config = create_client_config(panel_url, jwt)
        self._client = Client(**config)
        settings = InternalSettings()
        self.chunk_size = settings.CHUNK_SIZE

    def ls(self, path: str = "in") -> List[dict]:
        # from kodosumi.helper import debug
        # debug()
        api_path, root = build_file_path(self.fid, path)
        resp = self._client.get(api_path)
        validate_response(resp, root)
        all_files = resp.json()
        return all_files

    def open(self, 
             path: str) -> "SyncFileStream":
        api_path, sub_path = build_file_path(self.fid, path)
        stream_context = self._client.stream("GET", api_path)
        return SyncFileStream(self, sub_path, stream_context)

    def remove(self, path: str) -> bool:
        api_path, sub_path = build_file_path(self.fid, path)
        resp = self._client.delete(api_path)
        if resp.status_code == 204:
            return True
        raise FileNotFoundError(sub_path)
    
    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def download(self, path: str = "in"):
        local_folder = mkdtemp(prefix="kodosumi-")
        root, *subpath = path.strip("/").split('/', 1)
        found = False
        search = Path(root) / "/".join(subpath)
        search_len = len(search.parts)
        for item in self.ls(root):
            in_scope = False
            if not subpath:
                in_scope = True
                parent = Path(root)
                found = True
            else:
                item_path = Path(item["path"])
                if search == item_path:
                    in_scope = True
                    parent = search.parent
                    found = True
                elif search_len < len(item_path.parts):
                    if search.parts == item_path.parts[0:search_len]:
                        in_scope = True
                        parent = search
                        found = True
            if not in_scope:
                continue
            relative_path = Path(item['path']).relative_to(parent)
            target_path = Path(local_folder).joinpath(relative_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with self.open(item['path']) as src:
                with open(str(target_path), "wb") as dst:
                    for chunk in src.read():
                        dst.write(chunk)
                yield str(target_path)
        if not found:
            raise FileNotFoundError(path)
        
    def upload(self, path: str):
        batch_response = self._client.post("/files/init_batch")
        assert batch_response.status_code == 201
        batch_id = batch_response.json()["batch_id"]
        
        path_obj = Path(path)
        base_path = path_obj.parent if path_obj.is_file() else path_obj
        files_to_upload = get_files_to_upload(path)
        if not files_to_upload:
            return None

        upload_ids = []
        
        for file in files_to_upload:
            total_chunks = calculate_total_chunks(
                file.stat().st_size, self.chunk_size)
            init_payload = create_upload_payload(
                file, base_path, total_chunks, batch_id)
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
                    chunk_data = fh.read(self.chunk_size)
                    if not chunk_data:
                        break
                    form_data, files = create_chunk_payload(
                        upload_id, chunk_num, chunk_data)
                    response = self._client.post(
                        "/files/chunk", data=form_data, files=files)
                    assert response.status_code == 201
                    data = response.json()
                    assert data["status"] == "chunk received"
                    assert data["chunk_number"] == chunk_num
            items[upload_id] = create_completion_payload(
                file, base_path, total_chunks)
        response = self._client.post(
            f"/files/complete/{self.fid}/{batch_id}/out", json=items)
        assert response.status_code == 201
        return batch_id
