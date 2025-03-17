import asyncio
import inspect
import queue
import sys
import threading
import time
from traceback import format_exc
from typing import Any, Callable, Optional, Tuple, Union

import ray
from bson.objectid import ObjectId
from pydantic import BaseModel

from kodosumi.dtypes import DynamicModel
from kodosumi.helper import now


EVENT_META    = "meta"
EVENT_AGENT   = "agent"
EVENT_DEBUG   = "debug"
EVENT_STATUS  = "status"
EVENT_ERROR   = "error"
EVENT_DATA    = "data"
EVENT_RESULT  = "result"
EVENT_ACTION  = "action"
EVENT_INPUTS  = "inputs"
EVENT_FINAL   = "final"
EVENT_STDOUT  = "stdout"
EVENT_STDERR  = "stderr"

STATUS_STARTING = "starting"
STATUS_RUNNING  = "running"
STATUS_END      = "finished"
STATUS_ERROR    = "error"
STATUS_FINAL    = (STATUS_END, STATUS_ERROR)

NAMESPACE = "kodosumi"
KODOSUMI_LAUNCH = "kodosumi_launch"

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


def parse_entry_point(entry_point: str) -> Callable:
    if ":" in entry_point:
        module_name, obj = entry_point.split(":", 1)
    else:
        *module_name, obj = entry_point.split(".")
        module_name = ".".join(module_name)
    module = __import__(module_name)
    components = module_name.split('.')
    for comp in components[1:]:
        module = getattr(module, comp)
    return getattr(module, obj)


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


class Tracer:

    def __init__(self, runner):
        self.runner = runner

    async def async_debug(self, message):
        await self.runner.enqueue({
            "timestamp": now(),
            "kind": EVENT_DEBUG,
            "payload": f"{message}"
        })

    async def async_result(self, message):
        await self.runner.enqueue({
            "timestamp": now(),
            "kind": EVENT_RESULT,
            "payload": _serialize(message)
        })

    async def async_action(self, message):
        await self.runner.enqueue({
            "timestamp": now(),
            "kind": EVENT_ACTION,
            "payload": _serialize(message)
        })

    def debug(self, message):
        self.runner.sync_enqueue({
            "timestamp": now(),
            "kind": EVENT_DEBUG,
            "payload": f"{message}"
        })

    def result(self, message):
        self.runner.sync_enqueue({
            "timestamp": now(),
            "kind": EVENT_RESULT,
            "payload": _serialize(message)
        })

    def action(self, message):
        self.runner.sync_enqueue({
            "timestamp": now(),
            "kind": EVENT_ACTION,
            "payload": _serialize(message)
        })

@ray.remote
class Runner:
    def __init__(self, 
                 fid: str, 
                 username: str, 
                 base_url: str, 
                 entry_point: Union[Callable, str], 
                 inputs: Any=None, 
                 extra: Optional[dict]=None):
        self.fid = fid
        self.username = username
        self.base_url = base_url
        self.entry_point = entry_point
        self.inputs = inputs
        self.extra = extra
        self.queue: asyncio.Queue = asyncio.Queue()
        self.active = True
        self.loop = _get_event_loop()
        self.logging_pipeline = LoggingPipeline(self.enqueue_batch, self.loop)
        self.logging_pipeline.start()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = StdOutHandler(self)
        sys.stderr = StdErrHandler(self)

    async def get_username(self):
        return self.username

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
        # from kodosumi import helper
        # helper.debug()
        final_kind = STATUS_END
        try:
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
            while not self.queue.empty():
                await asyncio.sleep(0.1)
            self.active = False

    async def start(self):
        # from kodosumi import helper
        # helper.debug()
        await self.enqueue({
            "timestamp": now(),
            "kind": EVENT_STATUS,
            "payload": STATUS_STARTING
        })
        await self.enqueue({
            "timestamp": now(),
            "kind": EVENT_INPUTS,
            "payload": _serialize(self.inputs)
        })
        if not isinstance(self.entry_point, str):
            ep = self.entry_point
            rep_entry_point = f"{ep.__module__}:{ep.__name__}"
        else:
            rep_entry_point = self.entry_point

        if isinstance(self.entry_point, str):
            obj = parse_entry_point(self.entry_point)
        else:
            obj = self.entry_point
        
        origin = {
            "summary": None, 
            "description": None, 
            "author": None, 
            "organization": None
        }
        if hasattr(obj, "__brief__"):
            for field in origin:
                origin[field] = obj.__brief__.get(field, None)
        elif hasattr(obj, "__doc__") and obj.__doc__:
            origin["summary"] = obj.__doc__.strip().split("\n")[0]
            origin["description"] = obj.__doc__.strip()

        await self.enqueue({
            "timestamp": now(),
            "kind": EVENT_META,
            "payload": _serialize({
                **{
                    "fid": self.fid,
                    "username": self.username,
                    "base_url": self.base_url,
                    "entry_point": rep_entry_point,
                    "extra": self.extra
                }, **origin
            })
        })

        await self.enqueue({
            "timestamp": now(),
            "kind": EVENT_STATUS,
            "payload": STATUS_RUNNING
        })
        tracer = Tracer(self)
        if hasattr(obj, "is_crew_class"):
            obj = obj().crew()
        if hasattr(obj, "kickoff_async"):
            obj.step_callback = tracer.action # type: ignore
            obj.task_callback = tracer.result  # type: ignore
            if isinstance(self.inputs, BaseModel):
                data = self.inputs.model_dump()
            else:
                data = self.inputs
            await self.summary(obj)
            result = await obj.kickoff_async(inputs=data)
        else:
            sig = inspect.signature(obj)
            bound_args = sig.bind_partial()
            if 'inputs' in sig.parameters:
                bound_args.arguments['inputs'] = self.inputs
            if 'trace' in sig.parameters:
                bound_args.arguments['trace'] = tracer
            bound_args.apply_defaults()
            if asyncio.iscoroutinefunction(obj):
                result = await obj(*bound_args.args, **bound_args.kwargs)
            else:
                result = await self.loop.run_in_executor(
                    None, obj, *bound_args.args, **bound_args.kwargs)

        await self.final(result)
        return result
    
    async def summary(self, flow):
        for agent in flow.agents:
            dump = {
                "role": agent.role,
                "goal": agent.goal,
                "backstory": agent.backstory,
                "tools": []
            }
            for tool in agent.tools:
                dump["tools"].append({
                    "name": tool.name,
                    "description": tool.description
                })
            await self.enqueue({
                "timestamp": now(),
                "kind": EVENT_AGENT,
                "payload": _serialize({"agent": dump})
            })
        for task in flow.tasks:
            dump = {
                "name": task.name,
                "description": task.description,
                "expected_output": task.expected_output,
                "agent": task.agent.role,
                "tools": []
            }
            for tool in agent.tools:
                dump["tools"].append({
                    "name": tool.name,
                    "description": tool.description
                })
            await self.enqueue({
                "timestamp": now(),
                "kind": EVENT_AGENT,
                "payload": _serialize({"task": dump})
            })

    async def shutdown(self):
        self.logging_pipeline.stop()
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        return "Runner shutdown complete."


def kill_runner(fid: str):
    runner = ray.get_actor(fid, namespace=NAMESPACE)
    ray.kill(runner)


def create_runner(username: str, 
                  base_url: str,
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
            base_url="/-" + base_url,
            entry_point=entry_point, 
            inputs=inputs, 
            extra=extra
    )
    return fid, actor
