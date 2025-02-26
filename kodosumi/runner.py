import asyncio
import inspect
import os
import sys
from traceback import format_exc
from typing import Any, Optional

import ray
from pydantic import BaseModel
from ray.util.queue import Queue

from kodosumi import helper
from kodosumi.dtypes import DynamicModel


NAMESPACE = "kodosumi"
SLEEP = 0.01

STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_END = "finished"
STATUS_ERROR = "error"
STATUS_FINAL = (STATUS_END, STATUS_ERROR)

EVENT_STDOUT = "stdout"
EVENT_STDERR = "stderr"
EVENT_DEBUG = "debug"
EVENT_INPUTS = "inputs"
EVENT_STATUS = "status"
EVENT_DATA = "data"
EVENT_META = "meta"
EVENT_ACTION = "action"
EVENT_RESULT = "result"
EVENT_FINAL = "final"
EVENT_ERROR = "error"


class StdOutHandler:

    prefix = EVENT_STDOUT

    def __init__(self, runner):
        self.runner = runner

    def write(self, message):
        if message.rstrip():
            self.runner.enqueue(
                self.prefix, "\\n".join(message.rstrip().splitlines()))

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
        await self.runner.async_enqueue(EVENT_DEBUG, message)

    async def result(self, message):
        await self.runner.async_enqueue(EVENT_RESULT, message)

    async def final(self, message):
        await self.runner.async_enqueue(EVENT_FINAL, message)


@ray.remote
class Runner:

    def __init__(self):
        self._entry_point = None
        self._user = None
        self._queue = Queue()
        self._loop = asyncio.get_event_loop()
        self._original_stdout = sys.stdout
        self._original_stderr = sys.stderr
        sys.stdout = StdOutHandler(self)
        sys.stderr = StdErrHandler(self)

    async def empty(self):
        return self._queue.empty()

    async def stop(self):
        sys.stdout = self._original_stdout
        sys.stderr = self._original_stderr
        while not self._queue.empty():
            await asyncio.sleep(SLEEP)
        self._queue.shutdown()

    def action(self, *args, **kwargs):
        self.enqueue(EVENT_ACTION, args[0])

    def result(self, *args, **kwargs):
        self.enqueue(EVENT_RESULT, args[0])

    def final(self, *args, **kwargs):
        self.enqueue(EVENT_FINAL, args[0])

    async def run(
            self, 
            user: str, 
            fid: str, 
            entry_point: str, 
            inputs: BaseModel,
            extra: Optional[dict] = None):
        #helper.debug()
        t0 = helper.now()
        await self.set_user(user)
        try:
            if not isinstance(entry_point, str):
                rep_ep = f"{entry_point.__module__}:{entry_point.__name__}"
            else:
                rep_ep = entry_point
            await self.set_entry_point(rep_ep)
            await self.async_enqueue(EVENT_STATUS, STATUS_STARTING)
            await self.async_enqueue(EVENT_DATA, {
                "entry_point": rep_ep,
                "user": user,
                "fid": fid,
                "pid": os.getpid(),
                "extra": extra
            })
            await self.start(entry_point, inputs)
        except Exception as exc:
            await self.async_enqueue(EVENT_ERROR, {
                "exception": exc.__class__.__name__, 
                "traceback": format_exc()})
            await self.async_enqueue(EVENT_STATUS, STATUS_ERROR)
        finally:
            runtime = helper.now() - t0
            await self.async_enqueue(
                EVENT_DATA, {"runtime": runtime.total_seconds()})
            await self.async_enqueue(EVENT_STATUS, STATUS_END)

    async def get_user(self):
        return self._user

    async def set_user(self, user):
        self._user = user

    async def get_entry_point(self):
        return self._entry_point

    async def set_entry_point(self, entry_point):
        self._entry_point = entry_point

    async def start(self, entry_point: Any, inputs: BaseModel):
        await self.async_enqueue(EVENT_INPUTS, inputs)
        await self.async_enqueue(EVENT_STATUS, STATUS_RUNNING)
        if isinstance(entry_point, str):
            flow = helper.parse_factory(entry_point)
        else:
            flow = entry_point
        if hasattr(flow, "is_crew_class"):
            flow = flow().crew()
        if hasattr(flow, "kickoff_async"):
            flow.step_callback = self.action  # type: ignore
            flow.task_callback = self.result  # type: ignore
            if isinstance(inputs, BaseModel):
                data = inputs.model_dump()
            else:
                data = inputs
            await self.summary(flow)
            result = await flow.kickoff_async(inputs=data)
        else:
            run_handler = RunHandler(self)
            sig = inspect.signature(flow)
            bound_args = sig.bind_partial()
            if 'inputs' in sig.parameters:
                bound_args.arguments['inputs'] = inputs
            if 'handler' in sig.parameters:
                bound_args.arguments['handler'] = run_handler
            bound_args.apply_defaults()
            result = await flow(*bound_args.args, **bound_args.kwargs)
        self.final(result)
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
            await self.async_enqueue(EVENT_META, {"agent": dump})
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
            await self.async_enqueue(EVENT_META, {"task": dump})

    def enqueue(self, key, data):
        self._loop.create_task(self.async_enqueue(key, data))

    def dequeue(self):
        return self._loop.create_task(self.async_dequeue())

    async def async_enqueue(self, key, data):
        await asyncio.sleep(SLEEP)
        await self._queue.put_async(
            (helper.now().isoformat(), key, self.serialize(data)))

    def serialize(self, data):
        # helper.debug()
        if isinstance(data, (str, int, float, bool)):
            return f"*{data}"
        if isinstance(data, BaseModel):
            dump = {data.__class__.__name__: data.model_dump()}
        elif isinstance(data, dict):
            dump = data
        elif hasattr(data, "__dict__"):
            dump = {data.__class__.__name__: data.__dict__}
        elif hasattr(data, "__slots__"):
            dump = {data.__class__.__name__: {
                k: getattr(data, k) for k in data.__slots__}}
        else:
            dump = {"TypeError": str(data)}
        return DynamicModel(dump).model_dump_json()        

    async def async_dequeue(self):
        ret = None
        try:
            if self._queue and not self._queue.empty():
                ret = await self._queue.get_async(block=False)
        except: pass
        await asyncio.sleep(SLEEP)
        return ret
