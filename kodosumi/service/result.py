import asyncio
import datetime
from pathlib import Path
from typing import AsyncGenerator, Union

import aiofiles
import ray
from ray._private.state import actors

from kodosumi import helper
from kodosumi.dtypes import DynamicModel
from kodosumi.log import logger
from kodosumi.runner import (EVENT_DATA, EVENT_FINAL, EVENT_RESULT,
                             EVENT_STATUS, NAMESPACE)
from kodosumi.spooler import EVENT_LOG_FILE, STDERR_FILE, STDOUT_FILE


_fields = ('ActorID', 'ActorClassName', 'IsDetached', 'Name', 'JobID', 
           'Address', 'OwnerAddress', 'State', 'NumRestarts', 'Timestamp', 
           'StartTime', 'EndTime', 'Pid')

TIMEOUT = 5
STREAM_INTERVAL = 0.01

class ExecutionResult:

    def __init__(self, exec_dir: Union[str, Path]):
        folder = Path(exec_dir)
        self.fid = folder.name
        self.event_log = folder.joinpath(EVENT_LOG_FILE)
        self.stdout_file = folder.joinpath(STDOUT_FILE)
        self.stderr_file = folder.joinpath(STDERR_FILE)
        self.tearup = None
        self.teardown = None
        self.lifetime = None
        self.status = None
        self.data = helper.DotDict()
        self._meta: list = []

    def _valid(self, other) -> bool:
        return (isinstance(other, ExecutionResult) 
                and (self.tearup is not None)
                and (other.tearup is not None))

    def __eq__(self, other):
        return self._valid(other) and self.tearup == other.tearup

    def __ne__(self, other):
        return self._valid(other) and self.tearup != other.tearup

    def __lt__(self, other):
        return self._valid(other) and self.tearup < other.tearup

    def __le__(self, other):
        return self._valid(other) and self.tearup <= other.tearup

    def __gt__(self, other):
        return self._valid(other) and self.tearup > other.tearup

    def __ge__(self, other):
        return self._valid(other) and self.tearup >= other.tearup

    async def _read_state(self) -> AsyncGenerator:
        self.tearup = teardown = self.data.runtime = None
        async for timestamp, action, value in self._read_event():
            if action == EVENT_STATUS:
                self.status = value
            elif action == EVENT_DATA:
                self.data.update(value)
            if self.tearup is None: self.tearup = timestamp
            teardown = timestamp
            yield timestamp, action, value
        if self.tearup:
            if self.data.runtime:
                self.teardown = teardown
                lifetime = self.teardown - self.tearup
            else:
                lifetime = helper.now() - self.tearup
            self.lifetime = lifetime.total_seconds()
        else:
            self.lifetime = None
    
    async def read_state(self) -> dict:
        async for timestamp, action, value in self._read_state():
            pass
        return self.get_state()
    
    async def is_alive(self) -> dict:
        t0 = helper.now()
        try:
            helper.ray_init(ignore_reinit_error=True)
            a = ray.get_actor(self.fid, namespace=NAMESPACE)
            aid = a._ray_actor_id.hex()
            state = actors().get(aid, {})
            info = {k: state.get(k, None) for k in _fields}
        except ValueError:
            info = None
        finally:
            #helper.ray_shutdown()
            pass
        return {
            "actor": info,
            "retrieval_time": (helper.now() - t0).total_seconds()
        } 

    def get_state(self) -> dict:
        state = self.data.copy()
        state["status"] = self.status
        state["tearup"] = self.tearup
        state["teardown"] = self.teardown
        state["lifetime"] = self.lifetime
        return state

    async def _read_event(self):
        if not self.event_log.is_file():
            return
        async with aiofiles.open(self.event_log, "r") as fh:
            async for line in fh:
                line = line.rstrip()
                if not line:
                    continue
                sts1, _, action, sdata = line.split(" ", 3)
                timestamp = datetime.datetime.fromisoformat(sts1)
                if sdata.startswith("*"):
                    data = sdata[1:]
                else:
                    data = DynamicModel.model_validate_json(sdata).model_dump()
                yield timestamp, action, data

    async def read_final(self) -> dict:
        final = None
        async for timestamp, action, value in self._read_state():
            if action == EVENT_FINAL:
                final = value
        ret = self.get_state()
        ret.update({"final": final})
        return ret

    async def read_result(self) -> dict:
        result = []
        final = None
        async for timestamp, action, value in self._read_state():
            if action == EVENT_RESULT:
                result.append(value)
            elif action == EVENT_FINAL:
                final = value
        ret = self.get_state()
        ret.update({"result": result, "final": final})
        return ret

    async def _reader(self, file, queue):
        try:
            check = None
            async with aiofiles.open(file, "r") as fh:
                while True:
                    line = await fh.readline()
                    now = helper.now()
                    if not line:
                        if ((not check) 
                                or ((now - check).total_seconds() >= TIMEOUT)):
                            check = now
                            info = await self.is_alive()
                            if not info.get("actor"):
                                break
                    else:
                        check = now
                        _, _, message = line.rstrip().split(" ", 2)
                        await queue.put(line)
                        if message.strip() in ("status *finished", "status *error"):
                            break
                    await asyncio.sleep(STREAM_INTERVAL)
        except Exception as exc:
            logger.error(f"streaming {file} failed: {exc}")
        finally:
            await queue.put(None)

    async def _read_file(self, file) -> AsyncGenerator[str, None]:

        queue: asyncio.Queue = asyncio.Queue()
        task1 = asyncio.create_task(self._reader(file, queue))

        while True:
            line = await queue.get()
            if line is None:
                if task1.done():
                    break
                continue
            yield line

        await task1

    async def follow(self) -> AsyncGenerator[str, None]:
        async for line in self._read_file(self.event_log):
            yield line
        logger.debug(f"stop following {self.event_log}")
