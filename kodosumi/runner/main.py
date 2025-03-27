import asyncio
import inspect
import sys
from traceback import format_exc
from typing import Any, Callable, Optional, Tuple, Union

import ray
import ray.util.queue
from bson.objectid import ObjectId
from pydantic import BaseModel

from kodosumi.helper import now, serialize
from kodosumi.runner.const import (EVENT_AGENT, EVENT_ERROR, EVENT_FINAL,
                                   EVENT_INPUTS, EVENT_META, EVENT_STATUS,
                                   NAMESPACE, STATUS_END, STATUS_ERROR,
                                   STATUS_RUNNING, STATUS_STARTING)
from kodosumi.runner.tracer import StderrHandler, StdoutHandler, Tracer


class MessageQueue:
    def __init__(self, batch_size: int = 10, batch_timeout: float = 0.1):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self._shutdown = asyncio.Event()
        self._lock = asyncio.Lock()
        self._is_flushing = False

    async def put(self, message: dict) -> None:
        await self.queue.put(message)

    async def get_batch(self,
                        size: Optional[int] = None,
                        timeout: Optional[float] = None) -> list[dict]:
        batch: list[dict] = []
        try:
            message = await asyncio.wait_for(
                self.queue.get(),
                timeout=timeout if timeout is not None else self.batch_timeout)
            batch.append(message)

            max_size = size if size is not None else self.batch_size
            for _ in range(max_size - 1):
                try:
                    message = self.queue.get_nowait()
                    batch.append(message)
                except asyncio.QueueEmpty:
                    break
        except asyncio.TimeoutError:
            pass

        return batch

    async def flush(self):
        async with self._lock:
            if self._is_flushing:
                return
            self._is_flushing = True
        try:
            while not self.queue.empty():
                batch = await self.get_batch()
                if batch:
                    yield batch
        finally:
            async with self._lock:
                self._is_flushing = False

    def is_empty(self) -> bool:
        return self.queue.empty()

    async def shutdown(self) -> None:
        self._shutdown.set()
        while not self.queue.empty():
            await asyncio.sleep(0.1)

def parse_entry_point(entry_point: str) -> Callable:
    if ":" in entry_point:
        module_name, obj = entry_point.split(":", 1)
    else:
        *mod_list, obj = entry_point.split(".")
        module_name = ".".join(mod_list)
    module = __import__(module_name)
    components = module_name.split('.')
    for comp in components[1:]:
        module = getattr(module, comp)
    return getattr(module, obj)



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
        self.active = True
        #self.message_queue = MessageQueue()
        self.message_queue = ray.util.queue.Queue()
        self.tracer = Tracer(self.message_queue)

        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = StdoutHandler(self.tracer)
        sys.stderr = StderrHandler(self.tracer)

    async def get_username(self):
        return self.username

    async def get_queue(self):
        return self.message_queue

    def is_active(self):
        return self.active

    async def run(self):
        # from kodosumi.helper import debug
        # debug()
        # breakpoint()
        final_kind = STATUS_END
        try:
            await self.start()
        except Exception as exc:
            final_kind = STATUS_ERROR
            await self._put_async(EVENT_ERROR, format_exc())
        finally:
            await self._put_async(EVENT_STATUS, final_kind)
            await self.shutdown()

    async def _put_async(self, kind: str, payload: Any):
        await self.message_queue.put_async({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  

    def _put(self, kind: str, payload: Any):
        self.message_queue.put({
            "timestamp": now(), 
            "kind": kind, 
            "payload": payload
        })  

    async def start(self):
        await self._put_async(EVENT_STATUS, STATUS_STARTING)
        await self._put_async(EVENT_INPUTS, serialize(self.inputs))
        if not isinstance(self.entry_point, str):
            ep = self.entry_point
            module = getattr(ep, "__module__", None)
            name = getattr(ep, "__name__", repr(ep))
            rep_entry_point = f"{module}.{name}"
        else:
            rep_entry_point = self.entry_point
        if isinstance(self.entry_point, str):
            obj = parse_entry_point(self.entry_point)
        else:
            obj = self.entry_point
        origin = {}
        if isinstance(self.extra, dict):
            for field in ("tags", "summary", "description", "deprecated"):
                origin[field] = self.extra.get(field, None)
            extra = self.extra.get("openapi_extra", {})
            for field in ("author", "organization", "version"):
                origin[field] = extra.get(f"x-{field}", None)
        await self._put_async(EVENT_META, serialize({
            **{
                "fid": self.fid,
                "username": self.username,
                "base_url": self.base_url,
                "entry_point": rep_entry_point
            }, 
            **origin}))
        await self._put_async(EVENT_STATUS, STATUS_RUNNING)
        # obj is a decorated crew class
        if hasattr(obj, "is_crew_class"):
            obj = obj().crew()

        # obj is a crew
        if hasattr(obj, "kickoff"):
            # obj.step_callback = tracer.action
            # obj.task_callback = tracer.result
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
            if 'tracer' in sig.parameters:
                bound_args.arguments['tracer'] = self.tracer
            bound_args.apply_defaults()
            if asyncio.iscoroutinefunction(obj):
                result = await obj(*bound_args.args, **bound_args.kwargs)
            else:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, obj, *bound_args.args, **bound_args.kwargs)
        await self._put_async(EVENT_FINAL, serialize(result))
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
            await self.put((EVENT_AGENT, serialize({"agent": dump})))
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
            await self.put((EVENT_AGENT, serialize({"task": dump})))

    async def shutdown(self):
        try:
            while True:
                if self.message_queue.empty():
                    break
                await asyncio.sleep(0.1)
            await self.message_queue.shutdown()
        except: 
            pass
        self.active = False
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
