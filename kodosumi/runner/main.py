import asyncio
import inspect
from traceback import format_exc
from typing import Any, Callable, Optional, Tuple, Union

import ray.util.queue
from bson.objectid import ObjectId
from pydantic import BaseModel
from fastapi.responses import JSONResponse

import kodosumi.core
from kodosumi.helper import now, serialize
from kodosumi.runner.const import (EVENT_AGENT, EVENT_ERROR, EVENT_FINAL,
                                   EVENT_INPUTS, EVENT_META, EVENT_STATUS,
                                   NAMESPACE, STATUS_END, STATUS_ERROR,
                                   STATUS_RUNNING, STATUS_STARTING,
                                   KODOSUMI_LAUNCH)
from kodosumi.runner.tracer import Tracer


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
        self.message_queue = ray.util.queue.Queue()
        self.tracer = Tracer(self.message_queue)
        self.tracer.init()

    async def get_username(self):
        return self.username

    async def get_queue(self):
        return self.message_queue

    def is_active(self):
        return self.active

    async def run(self):
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
        origin = {"kodosumi": kodosumi.core.__version__}
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
            obj.step_callback = self.tracer.action_sync
            obj.task_callback = self.tracer.result_sync
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
            await self._put_async(EVENT_AGENT, serialize({"agent": dump}))
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
            await self._put_async(EVENT_AGENT, serialize({"task": dump}))

    async def shutdown(self):
        try:
            queue_actor = self.message_queue.actor
            while True:
                done, _ = ray.wait([
                    queue_actor.empty.remote()], timeout=0.01)
                if done:
                    ret = await asyncio.gather(*done)
                    if ret:
                        if ret[0] == True:
                            break
                await asyncio.sleep(0.1)
            self.tracer.shutdown()
            self.message_queue.shutdown()
        except: 
            pass
        self.active = False
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

def Launch(request: Any,
           entry_point: Union[Callable, str], 
           inputs: Any=None,
           reference: Optional[Callable] = None,
           summary: Optional[str] = None,
           description: Optional[str] = None) -> Any:
    if reference is None:
        for sf in inspect.stack():
            if getattr(sf.frame.f_globals.get(sf.function), "_kodosumi_", None):
                reference = sf.frame.f_globals.get(sf.function)
                break
    if reference is None:
        extra = {}
    else:
        extra = request.app._method_lookup.get(reference)
    if summary is not None:
        extra["summary"] = summary
    if description is not None:
        extra["description"] = description
    fid, runner = create_runner(
        username=request.state.user, base_url=request.state.prefix, 
        entry_point=entry_point, inputs=inputs, extra=extra)
    runner.run.remote()  # type: ignore
    return JSONResponse(content={"fid": fid}, headers={KODOSUMI_LAUNCH: fid})
