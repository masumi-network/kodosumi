from enum import Enum, auto

from httpx import AsyncClient, Client, Response


class StreamState(Enum):
    FRESH = auto()
    OPEN = auto()
    CLOSED = auto()


class FileStream:

    def __init__(self, fs: "FileSystem", path: str, stream_context):
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


class FileSystem:
    def __init__(self, fid: str, panel_url: str, jwt: str):
        self.fid = fid
        self.panel_url = panel_url
        self.jwt = jwt
        self._client = AsyncClient(
            timeout=300, cookies={"kodosumi_jwt": self.jwt},
            base_url=f"{self.panel_url}/files/{self.fid}")

    @property
    def url(self):
        return f"{self.panel_url}/files/{self.fid}/in"

    async def ls(self, root: str = "in"):
        resp = await self._client.get(root)
        resp.raise_for_status()
        return resp.json()

    def open(self, path: str) -> "FileStream":
        stream_context = self._client.stream("GET", f"in/{path}")
        return FileStream(self, path, stream_context)

    async def remove(self, path: str) -> bool:
        resp = await self._client.delete(f"in/{path}")
        if resp.status_code != 204:
            raise FileNotFoundError(path)
        return True
    
    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class SyncFileSystem:
    def __init__(self, fid: str, panel_url: str, jwt: str):
        self.fid = fid
        self.panel_url = panel_url
        self.jwt = jwt
        self._client = Client(
            timeout=300, cookies={"kodosumi_jwt": self.jwt},
            base_url=f"{self.panel_url}/files/{self.fid}")

    @property
    def url(self):
        return f"{self.panel_url}/files/{self.fid}/in"

    def ls(self, root: str = "in"):
        resp = self._client.get(root)
        resp.raise_for_status()
        return resp.json()

    def open(self, path: str) -> "SyncFileStream":
        stream_context = self._client.stream("GET", f"in/{path}")
        return SyncFileStream(self, path, stream_context)

    def remove(self, path: str) -> bool:
        resp = self._client.delete(f"in/{path}")
        if resp.status_code != 204:
            raise FileNotFoundError(path)
        return True
    
    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
