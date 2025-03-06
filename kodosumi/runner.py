import asyncio
import inspect
import queue
import sys
import threading
import time
from traceback import format_exc
from typing import Any, Callable, Dict, Optional, Tuple, Union

import ray
from bson.objectid import ObjectId
from pydantic import BaseModel, RootModel

EVENT_META    = "meta"
EVENT_DEBUG   = "debug"
EVENT_STATUS  = "status"
EVENT_ERROR   = "error"
EVENT_DATA    = "data"
EVENT_RESULT  = "result"
EVENT_INPUTS  = "inputs"
EVENT_FINAL   = "final"
EVENT_STDOUT  = "stdout"
EVENT_STDERR  = "stderr"

STATUS_STARTING = "starting"
STATUS_RUNNING  = "running"
STATUS_END      = "finished"
STATUS_ERROR    = "error"

NAMESPACE = "kodosumi"


def now():
    return time.time()


class LoggingPipeline:
    def __init__(self, target, loop, size=10, timeout=0.1):
        self.target = target
        self.loop = loop
        self.size = size
        self.timeout = timeout
        self.queue = queue.Queue()
        self.shutdown = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.shutdown.set()
        self.thread.join()

    def log(self, message: dict):
        self.queue.put(message)

    def run(self):
        batch = []
        last_flush = time.time()

        def push(batch):
            future = asyncio.run_coroutine_threadsafe(
                self.target(batch), self.loop)
            try:
                future.result(timeout=1)
            except Exception as e:
                print(f"LoggingPipeline push error: {e}")

        while not self.shutdown.is_set():
            try:
                msg = self.queue.get(timeout=self.timeout)
                batch.append(msg)
            except queue.Empty:
                pass
            if (batch and (len(batch) >= self.size
                           or (time.time() - last_flush) >= self.timeout)):
                push(batch)
                batch = []
                last_flush = time.time()
        if batch:
            push(batch)


def _get_event_loop():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


class DynamicModel(RootModel[Dict[str, Any]]):
    pass


def _serialize(data):
    if isinstance(data, BaseModel):
        dump = {data.__class__.__name__: data.model_dump()}
    elif isinstance(data, (dict, str, int, float, bool)):
        dump = {data.__class__.__name__: data}
    elif hasattr(data, "__dict__"):
        dump = {data.__class__.__name__: data.__dict__}
    elif hasattr(data, "__slots__"):
        dump = {data.__class__.__name__: {
            k: getattr(data, k) for k in data.__slots__}}
    else:
        dump = {"TypeError": str(data)}
    return DynamicModel(dump).model_dump_json()


class StdOutHandler:
    prefix = EVENT_STDOUT

    def __init__(self, runner):
        self._runner = runner
        self._loop = _get_event_loop()

    def write(self, message):
        if message.rstrip():
            log_entry = {
                "timestamp": now(),
                "kind": self.prefix,
                "payload": message.rstrip()
            }
            self._runner.sync_enqueue(log_entry)

    def flush(self):
        pass

    def isatty(self):
        return False


class StdErrHandler(StdOutHandler):
    prefix = EVENT_STDERR


class RunHandler:

    def __init__(self, runner):
        self.runner = runner

    async def debug(self, message):
        await self.runner.enqueue({
            "timestamp": now(),
            "kind": EVENT_DEBUG,
            "payload": f"{message}"
        })

    async def result(self, message):
        await self.runner.enqueue({
            "timestamp": now(),
            "kind": EVENT_RESULT,
            "payload": _serialize(message)
        })


@ray.remote
class Runner:
    def __init__(self, fid, username, entry_point, inputs, extra=None):
        self.fid = fid
        self.username = username
        self.entry_point = entry_point
        self.inputs = inputs
        self.extra = extra
        self.queue = asyncio.Queue()
        self.active = True
        self.loop = _get_event_loop()
        self.logging_pipeline = LoggingPipeline(self.enqueue_batch, self.loop)
        self.logging_pipeline.start()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = StdOutHandler(self)
        sys.stderr = StdErrHandler(self)

    def is_active(self):
        return self.active
    
    async def enqueue_batch(self, batch):
        for msg in batch:
            await self.queue.put(msg)

    async def enqueue(self, payload: dict):
        await self.queue.put(payload)

    def sync_enqueue(self, payload: dict):
        self.logging_pipeline.log(payload)

    async def get_batch(self, size: int = 10, timeout: float = 0.1):
        payload = []
        try:
            load = await asyncio.wait_for(self.queue.get(), timeout=timeout)
            payload.append(load)
        except asyncio.TimeoutError:
            return payload
        for _ in range(size - 1):
            try:
                load = self.queue.get_nowait()
                payload.append(load)
            except asyncio.QueueEmpty:
                break
        return payload

    async def final(self, payload):
        await self.enqueue({
            "timestamp": now(),
            "kind": EVENT_FINAL,
            "payload": _serialize(payload)
        })

    async def run(self):
        # helper.debug()
        open("debug.log", "w").write(f"actor {self.fid} started\n")
        final_kind = STATUS_END
        try:
            if not isinstance(self.entry_point, str):
                ep = self.entry_point
                rep_entry_point = f"{ep.__module__}:{ep.__name__}"
            else:
                rep_entry_point = self.entry_point
            # Log metadata and starting status.
            await self.enqueue({
                "timestamp": now(),
                "kind": EVENT_META,
                "payload": _serialize({
                    "fid": self.fid,
                    "username": self.username,
                    "entry_point": rep_entry_point,
                    "extra": self.extra
                })
            })
            await self.enqueue({
                "timestamp": now(),
                "kind": EVENT_STATUS,
                "payload": STATUS_STARTING
            })
            await self.start()
        except Exception as exc:
            final_kind = STATUS_ERROR
            await self.enqueue({
                "timestamp": now(),
                "kind": EVENT_ERROR,
                "payload": format_exc()
            })
        finally:
            await self.enqueue({
                "timestamp": now(),
                "kind": EVENT_STATUS,
                "payload": final_kind
            })
            open("debug.log", "a").write(f"actor {self.fid} waits\n")
            while not self.queue.empty():
                await asyncio.sleep(0.1)
            self.active = False
        open("debug.log", "a").write(f"actor {self.fid} stopped\n")

    async def start(self):
        await self.enqueue({
            "timestamp": now(),
            "kind": EVENT_INPUTS,
            "payload": _serialize(self.inputs)
        })
        await self.enqueue({
            "timestamp": now(),
            "kind": EVENT_STATUS,
            "payload": STATUS_RUNNING
        })
        if isinstance(self.entry_point, str):
            module_name, func_name = self.entry_point.split(":")
            module = __import__(module_name)
            func = getattr(module, func_name)
        else:
            func = self.entry_point

        run_handler = RunHandler(self)
        sig = inspect.signature(func)
        bound_args = sig.bind_partial()
        if 'inputs' in sig.parameters:
            bound_args.arguments['inputs'] = self.inputs
        if 'handler' in sig.parameters:
            bound_args.arguments['handler'] = run_handler
        bound_args.apply_defaults()
        if asyncio.iscoroutinefunction(func):
            result = await func(*bound_args.args, **bound_args.kwargs)
        else:
            result = await self.loop.run_in_executor(
                None, func, *bound_args.args, **bound_args.kwargs)

        await self.final(result)
        return result

    async def shutdown(self):
        self.logging_pipeline.stop()
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        return "Runner shutdown complete."


def create_runner(username: str, 
                  entry_point: Union[str, Callable],
                  inputs: Union[BaseModel, dict], 
                  extra: Optional[dict] = None,
                  fid: Optional[str]= None) -> Tuple[str, Runner]:
    if fid is None:
        fid = str(ObjectId())
    actor = Runner.options(  # type: ignore
        namespace=NAMESPACE, 
        name=fid, 
        enable_task_events=False,
        lifetime="detached").remote(
            fid=fid, 
            username=username, 
            entry_point=entry_point, 
            inputs=inputs, 
            extra=extra
    )
    return fid, actor
