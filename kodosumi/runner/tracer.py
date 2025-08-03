import sys
import traceback
import uuid
from typing import Any, Optional

import ray.util.queue

from kodosumi import dtypes
from kodosumi.config import InternalSettings
from kodosumi.const import (EVENT_ACTION, EVENT_DEBUG, EVENT_LEASE, EVENT_LOCK,
                            EVENT_RESULT, EVENT_STDERR, EVENT_STDOUT,
                            NAMESPACE)
from kodosumi.helper import now, serialize
from kodosumi.runner.files import AsyncFileSystem, SyncFileSystem


class StdoutHandler:

    prefix = EVENT_STDOUT

    def __init__(self, tracer):
        self._tracer = tracer

    def write(self, message: str) -> None:
        if not message.rstrip():
            return
        self._tracer._put(self.prefix, message.rstrip())

    def flush(self):
        pass

    def isatty(self) -> bool:
        return False

    def writelines(self, datas):
        for data in datas:
            self.write(data)


class StderrHandler(StdoutHandler):

    prefix = EVENT_STDERR


class Tracer:
    def __init__(self, 
                 fid: str, 
                 queue: ray.util.queue.Queue, 
                 panel_url: str,
                 jwt: str):
        self.fid = fid
        self.queue = queue
        self.panel_url = panel_url.rstrip("/")
        self.jwt = jwt
        self._init = False

    def __reduce__(self):
        deserializer = Tracer
        serialized_data = (self.fid, self.queue, self.panel_url, self.jwt)
        return deserializer, serialized_data

    def init(self):
        if not self._init:
            self._original_stdout = sys.stdout
            self._original_stderr = sys.stderr
            sys.stdout = StdoutHandler(self)
            sys.stderr = StderrHandler(self)
            self._init = True

    def shutdown(self):
        if self._init:
            sys.stdout = self._original_stdout
            sys.stderr = self._original_stderr

    async def _put_async(self, kind: str, payload: Any):
        self.init()
        await self.queue.put_async({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  

    def _put(self, kind: str, payload: Any):
        self.init()
        data = {
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        }
        self.queue.actor.put.remote(data)  # type: ignore

    async def debug(self, *message: str):
        await self._put_async(EVENT_DEBUG, "\n".join(message))

    def debug_sync(self, *message: str):
        self._put(EVENT_DEBUG, "\n".join(message))

    async def result(self, *message: Any):
        for m in message:
            await self._put_async(EVENT_RESULT, serialize(m))

    def result_sync(self, *message: Any):
        for m in message:
            self._put(EVENT_RESULT, serialize(m))

    async def action(self, *message: Any):
        for m in message:
            await self._put_async(EVENT_ACTION, serialize(m))

    def action_sync(self, *message: Any):
        for m in message:
            self._put(EVENT_ACTION, serialize(m))

    async def markdown(self, *message: str):
        await self._put_async(EVENT_RESULT, serialize(
            dtypes.Markdown(body="\n\n".join(message))))

    def markdown_sync(self, *message: str):
        self._put(EVENT_RESULT, serialize(
            dtypes.Markdown(body="\n\n".join(message))))

    async def html(self, *message: str):
        await self._put_async(EVENT_RESULT, serialize(
            dtypes.HTML(body="\n".join(message))))

    def html_sync(self, *message: str):
        self._put(EVENT_RESULT, serialize(
            dtypes.HTML(body="\n".join(message))))

    async def text(self, *message: str):
        await self._put_async(EVENT_RESULT, serialize(
            dtypes.Text(body="\n".join(message))))

    def text_sync(self, *message: str):
        self._put(EVENT_RESULT, serialize(
            dtypes.Text(body="\n".join(message))))

    async def warning(self, *message: str, exc_info: bool = False):
        output = list(message)
        if exc_info:
            output.append(traceback.format_exc())
        await self._put_async(EVENT_STDERR, "\n".join(output))

    def warning_sync(self, *message: str, exc_info: bool = False):
        output = list(message)
        if exc_info:
            output.append(traceback.format_exc())
        self._put(EVENT_STDERR, "\n".join(output))

    async def lock(self, 
                   name: str, 
                   data: Optional[dict] = None, 
                   timeout: Optional[float] = None):
        settings = InternalSettings()
        max_seconds = timeout or settings.LOCK_EXPIRES
        expires = now() + max_seconds
        lid = str(uuid.uuid4())
        lock_data = {"name": name, "lid": lid, "data": data, "expires": expires}
        await self._put_async(EVENT_LOCK, serialize(lock_data))
        runner = ray.get_actor(self.fid, namespace=NAMESPACE)
        result = await runner.lock.remote(name, lid, expires, data)
        lease_data = {"name": name, "lid": lid, "result": result}
        await self._put_async(EVENT_LEASE, serialize(lease_data))
        return result

    async def fs(self):
        return AsyncFileSystem(self.fid, self.panel_url, self.jwt)

    def fs_sync(self):
        return SyncFileSystem(self.fid, self.panel_url, self.jwt)
