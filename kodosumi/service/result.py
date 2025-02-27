import asyncio
import datetime
import pickle
from pathlib import Path
from typing import AsyncGenerator, Optional, Tuple, Union

import aiofiles
import ray
from ray._private.state import actors

from kodosumi import helper
from kodosumi.dtypes import DynamicModel
from kodosumi.log import logger
from kodosumi.runner import (EVENT_DATA, EVENT_STATUS, EVENT_STDERR,
                             EVENT_STDOUT, NAMESPACE, STATUS_ERROR,
                             STATUS_FINAL)
from kodosumi.spooler import (EVENT_LOG_FILE, PICKLE_FILE, STDERR_FILE,
                              STDOUT_FILE)


fromisoformat = datetime.datetime.fromisoformat

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
        self.pickle = folder.joinpath(PICKLE_FILE)
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

    async def read_state(self, timeout: Union[int, None]) -> dict:
        async for timestamp, action, value in self._read_state(timeout):
            pass
        return self.get_state()
    
    async def _read_state(self, timeout: Union[int, None]) -> AsyncGenerator:
        self.tearup = teardown = self.data.runtime = None
        async for timestamp, action, value in self._read_event(timeout):
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
    
    async def _read_event(self, timeout: Union[int, None]) -> AsyncGenerator:
        
        if timeout is not None:
            until = helper.now() + datetime.timedelta(seconds=timeout)
            if not self.event_log.is_file():
                logger.debug(f"waiting for {self.event_log}")
                while not self.event_log.is_file():
                    await asyncio.sleep(STREAM_INTERVAL)
                    if helper.now() >= until:
                        raise FileNotFoundError(f"{self.event_log} not found")

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

    async def is_alive(self) -> dict:
        t0 = helper.now()
        if self.status not in STATUS_FINAL:
            try:
                #helper.ray_init(ignore_reinit_error=True)
                a = ray.get_actor(self.fid, namespace=NAMESPACE)
                aid = a._ray_actor_id.hex()
                state = actors().get(aid, {})
                info = {k: state.get(k, None) for k in _fields}
            except ValueError:
                info = None
            finally:
                #helper.ray_shutdown()
                pass
        else:
            info = None
        return {
            "actor": info,
            "retrieval_time": (helper.now() - t0).total_seconds()
        } 

    def get_state(self) -> dict:
        state = dict(self.data.copy())
        state["status"] = self.status
        state["tearup"] = self.tearup
        state["teardown"] = self.teardown
        state["lifetime"] = self.lifetime
        return state

    def load(self):
        if self.pickle.exists():
            load = pickle.load(self.pickle.open("rb"))
            self.data = load["data"]
            self.status = load["status"]
            self.tearup = load["tearup"]
            self.teardown = load["teardown"]
            self.lifetime = load["lifetime"]
            return True
        return False

    def dump(self):
        if self.status in STATUS_FINAL:
            pickle.dump({
                'data': self.data,
                'status': self.status,
                'tearup': self.tearup, 
                'teardown': self.teardown,
                'lifetime': self.lifetime
            }, self.pickle.open("wb"), )        

    async def follow(self, 
                     timeout:Optional[int] = None,
                     quick: bool=False) -> AsyncGenerator[Tuple, None]:

        queue: asyncio.Queue = asyncio.Queue()
        self.tearup = None
        hardcut = min(timeout, TIMEOUT) if timeout else TIMEOUT

        async def _reader():
            try:
                check = helper.now()
                async with aiofiles.open(self.event_log, "r") as fh:
                    while True:
                        line = await fh.readline()
                        now = helper.now()
                        if not line:
                            if (((now - check).total_seconds() >= hardcut)):
                                check = now
                                info = await self.is_alive()
                                if not info.get("actor"):
                                    logger.warning(
                                        f"actor {self.event_log} not found")
                                    break
                            continue
                        t, _, event, payload = line.rstrip().split(" ", 3)
                        if not self.tearup:
                            self.tearup = fromisoformat(t)
                        self.teardown = fromisoformat(t)
                        runtime = (self.teardown - self.tearup).total_seconds()
                        if payload.startswith("*"):
                            payload = payload[1:] 
                        if event in (EVENT_STDERR, EVENT_STDOUT):
                            payload = payload.replace("\\n", "\n")
                            payload = ''.join(
                                c for c in payload if c.isprintable())
                        elif event == EVENT_STATUS:
                            self.status = payload
                            if payload in STATUS_FINAL:
                                await queue.put((t, runtime, event, payload))
                                break
                        elif event == EVENT_DATA:
                            data = DynamicModel.model_validate_json(
                                payload).model_dump()
                            self.data.update(data)
                        check = now
                        await queue.put((t, runtime, event, payload))
                        await asyncio.sleep(STREAM_INTERVAL)
                        if quick and self.status and "fid" in self.data:
                            logger.debug(
                                f"quick return {self.event_log}")
                            break
            except Exception as exc:
                logger.error(f"streaming {self.event_log} failed: {exc}")
                await queue.put((None, 'error'))
            else:
                await queue.put((None, 'success'))

        async def _wait():
            until = helper.now() + datetime.timedelta(seconds=timeout)
            if not self.event_log.is_file():
                logger.debug(f"waiting {timeout} for {self.event_log}")
                while not self.event_log.is_file():
                    await asyncio.sleep(STREAM_INTERVAL)
                    if helper.now() >= until:
                        return False
            return True
                    
        if timeout:
            if not await _wait():
                logger.warning(f"{self.event_log} not found")
                yield (None, 'error')

        task1 = asyncio.create_task(_reader())

        success = None
        while True:
            data = await queue.get()
            if data[0] is None:
                queue.task_done()
                if data[1] == 'error':
                    success = False
                elif data[1] == 'success':
                    success = True
                else:
                    logger.error(f"unexpected queue result: {data}")
                break
            yield data
            queue.task_done()

        await queue.join()
        await task1

        if not success:
            self.status = STATUS_ERROR

        if self.tearup:
            if self.teardown:
                lifetime = self.teardown - self.tearup
            else:
                lifetime = helper.now() - self.tearup
            self.lifetime = lifetime.total_seconds()
        else:
            self.lifetime = None
