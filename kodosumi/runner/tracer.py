import sys
from typing import Any

import ray.util.queue

from kodosumi import dtypes
from kodosumi.helper import now, serialize
from kodosumi.runner.const import (EVENT_ACTION, EVENT_DEBUG, EVENT_RESULT,
                                   EVENT_STDERR, EVENT_STDOUT)


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


class StderrHandler(StdoutHandler):

    prefix = EVENT_STDERR


class Tracer:
    def __init__(self, queue: ray.util.queue.Queue):
        self.queue = queue
        self._init = False

    def __reduce__(self):
        deserializer = Tracer
        serialized_data = (self.queue,)
        return deserializer, serialized_data

    def init(self):
        if not self._init:
            self._init = True
            self._original_stdout = sys.stdout
            self._original_stderr = sys.stderr
            sys.stdout = StdoutHandler(self)
            sys.stderr = StderrHandler(self)

    def shutdown(self):
        if self._init:
            sys.stdout = self._original_stdout
            sys.stderr = self._original_stderr
            # self.queue.shutdown()

    async def _put_async(self, kind: str, payload: Any):
        self.init()
        await self.queue.put_async({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  

    def _put(self, kind: str, payload: Any):
        self.init()
        self.queue.put({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  

    async def debug(self, message: str):
        await self._put_async(EVENT_DEBUG, message + "\n")

    def debug_sync(self, message: str):
        self._put(EVENT_DEBUG, message + "\n")

    async def result(self, message: Any):
        await self._put_async(EVENT_RESULT, serialize(message))

    def result_sync(self, message: Any):
        self._put(EVENT_RESULT, serialize(message))

    async def action(self, message: Any):
        await self._put_async(EVENT_ACTION, serialize(message))

    def action_sync(self, message: Any):
        self._put(EVENT_ACTION, serialize(message))

    async def markdown(self, message: str):
        await self._put_async(EVENT_RESULT, serialize(
            dtypes.Markdown(body=message)))

    async def html(self, message: str):
        await self._put_async(EVENT_RESULT, serialize(
            dtypes.HTML(body=message)))

    async def text(self, message: str):
        await self._put_async(EVENT_RESULT, serialize(
            dtypes.Text(body=message)))
