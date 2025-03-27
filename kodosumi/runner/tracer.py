import asyncio
import re
import sys
from typing import Any, Optional

import ray
import ray.util.queue

from kodosumi import dtypes
from kodosumi.helper import serialize, now
from kodosumi.runner.const import (EVENT_ACTION, EVENT_DEBUG, EVENT_RESULT,
                                   EVENT_STDERR, EVENT_STDOUT, NAMESPACE)


class StdoutHandler:

    prefix = EVENT_STDOUT

    def __init__(self, tracer):
        self._tracer = tracer
        # self._buffer = []
        # self._lock = asyncio.Lock()
        # self._loop = asyncio.get_event_loop()

    def write(self, message: str) -> None:
        if not message.rstrip():
            return
        self._tracer._put(self.prefix, message.rstrip())
        # self._loop.create_task(self._write(self.prefix, message.rstrip()))
        # self._write(self.prefix, message.rstrip())

    # async def _write(self, prefix: str, payload: Any):
    #     # async with self._lock:
    #     await self._tracer.put_async((prefix, payload))

    def flush(self):
        pass

    def isatty(self) -> bool:
        return False


class StderrHandler(StdoutHandler):

    prefix = EVENT_STDERR
    # pattern = re.compile(
    #     r'^\s*<x-(text|html|markdown)\s*>(.*?)</x-\1\s*>\s*$', re.I)

    # def write(self, message: str) -> None:
    #     if not message.rstrip():
    #         return
    #     match = self.pattern.match(message)
    #     if match:
    #         self._loop.create_task(
    #             self._tracer.put_async((EVENT_RESULT,
    #                 serialize(dtypes.format_map[match.group(1).lower()](
    #                     body=match.group(2).strip())))))
    #     else:
    #         super().write(message)


class Tracer:
    def __init__(self, queue: ray.util.queue.Queue):
        self.queue = queue
        # try:
        #     self._loop = asyncio.get_event_loop()
        # except RuntimeError:
        #     self._loop = asyncio.new_event_loop()
        #     asyncio.set_event_loop(self._loop)
        # self._runner = ray.get_actor(self.fid, namespace=NAMESPACE)

    # def __reduce__(self):
    #     deserializer = Tracer
    #     serialized_data = (self.fid,)
    #     return deserializer, serialized_data

    # def reset(self):
    #     self._original_stdout = sys.stdout
    #     self._original_stderr = sys.stderr
    #     sys.stdout = StdoutHandler(self)
    #     sys.stderr = StderrHandler(self)

    # def finish(self):
    #     sys.stdout = self._original_stdout
    #     sys.stderr = self._original_stderr

    async def _put_async(self, kind: str, payload: Any):
        await self.queue.put_async({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  

    def _put(self, kind: str, payload: Any):
        self.queue.put({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  



    # async def async_result(self, message: Any):
    #     await self._runner.put.remote(EVENT_RESULT, serialize(message))

    # def debug(self, message: str):
    #     asyncio.create_task(self.trigger(EVENT_DEBUG, message + "\n"))

    # def result(self, message: Any):
    #     asyncio.create_task(self.trigger(EVENT_RESULT, serialize(message)))

    # def action(self, message: Any):
    #     asyncio.create_task(self.trigger(EVENT_ACTION, serialize(message)))

    # def markdown(self, *args):
    #     self.result(dtypes.Markdown(body=" ".join(args)))

    # def chip(self, *args):
    #     self.result(
    #         dtypes.HTML(
    #             body=f"""
    #             <p><button> {" ".join(args)} </button></p>
    #             """
    #         )
    #     )

    # def html(self, *args):
    #     self.result(dtypes.HTML(body=" ".join(args)))

    # def text(self, *args):
    #     self.result(dtypes.Text(body=" ".join(args)))


def get_current_runner_fid():
    context = ray.get_runtime_context()
    name = context.get_actor_name()
    return name


def get_tracer(fid: Optional[str] = None):
    if fid is None:
        fid = get_current_runner_fid()
    if fid is not None:
        return Tracer(fid)
    return None
    

def _stderr(tag, *args):
    sys.stderr.write(
        f"<x-{tag}>{' '.join([str(a) for a in args])}</x-{tag}>")
    sys.stderr.flush()


def markdown(*args):
    _stderr("markdown", *args)

def html(*args):
    _stderr("html", *args)

def text(*args):
    _stderr("text", *args)

