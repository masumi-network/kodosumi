import asyncio
import datetime
from pathlib import Path

import litestar
from litestar import MediaType, Request, get
from litestar.datastructures import State
from litestar.response import Response, Stream, Template

from kodosumi import helper
from kodosumi.config import InternalSettings
from kodosumi.log import logger
from kodosumi.runner import EVENT_STDERR, EVENT_STDOUT
from kodosumi.service.result import ExecutionResult


fromisoformat = datetime.datetime.fromisoformat


class ExecutionControl(litestar.Controller):

    def _exec_path(self, state: State, request: Request) -> Path: 
        user = request.user.username
        settings: InternalSettings = state["settings"]
        return Path(settings.EXEC_DIR).joinpath(user)

    @get("/-/executions")
    async def list_executions(
            self, 
            request: Request, 
            state: State) -> Response:
        t0 = helper.now()
        exec_path = self._exec_path(state, request)
        if exec_path.exists() and exec_path.is_dir():
            executions = []
            for exec_dir in exec_path.iterdir():
                if not exec_dir.is_dir():
                    continue
                result = ExecutionResult(exec_dir)
                try:
                    await result.read_state()
                except:
                    raise RuntimeError(f"failed to read {exec_dir}")
                executions.append(result)
            executions.sort(reverse=True)
            data = [r.get_state() for r in executions]
        else:
            data = []
        if helper.wants(request, MediaType.HTML):
            return Template("executions.html", context={"executions": data})
        logger.info(f"GET /-/executions in {helper.now() - t0}")
        return Response(content=data)

    @get("/-/executions/{fid:str}")
    async def execution_detail(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Response:
        t0 = helper.now()
        file = self._exec_path(state, request).joinpath(fid)
        until = helper.now() + datetime.timedelta(
            seconds=state["settings"].WAIT_FOR_JOB)
        while helper.now() < until:
            logger.debug(f"waiting for {fid}")
            if file.exists():
                break
            await asyncio.sleep(1)
        if not file.exists():
            raise FileNotFoundError(f"{fid} not found")
        result = ExecutionResult(file)
        execution = await result.read_state()
        execution["alive"] = await result.is_alive()
        logger.info(f"GET /-/executions/{fid} in {helper.now() - t0}")
        if helper.wants(request, MediaType.HTML):
            return Template(
                "status.html", 
                context={"execution": execution})
        return Response(content=execution)

    @get("/-/executions/final/{fid:str}")
    async def execution_final(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Response:
        t0 = helper.now()
        file = self._exec_path(state, request).joinpath(fid)
        if not file.exists():
            raise FileNotFoundError(f"{fid} not found")
        result = ExecutionResult(file)
        ret = await result.read_final()
        logger.info(f"GET /-/executions/final/{fid} in {helper.now() - t0}")
        return Response(content=ret)

    @get("/-/executions/results/{fid:str}")
    async def execution_results(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Response:
        t0 = helper.now()
        file = self._exec_path(state, request).joinpath(fid)
        if not file.exists():
            raise FileNotFoundError(f"{fid} not found")
        result = ExecutionResult(file)
        ret = await result.read_result()
        logger.info(f"GET /-/executions/results/{fid} in {helper.now() - t0}")
        return Response(content=ret)

    @get("/-/executions/follow/{fid:str}")
    async def follow(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Stream:
        logger.debug(f"following {fid}")
        file = self._exec_path(state, request).joinpath(fid)
        until = helper.now() + datetime.timedelta(
            seconds=state["settings"].WAIT_FOR_JOB)
        while helper.now() < until:
            logger.debug(f"waiting for {fid}")
            if file.exists():
                break
            await asyncio.sleep(0.5)
        if not file.exists():
            raise FileNotFoundError(f"{fid} not found")
        result = ExecutionResult(file)
        return Stream(result.follow(), media_type="text/plain")

    @get("/-/executions/sse/{fid:str}")
    async def follow_sse(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Stream:
        logger.debug(f"streaming SSE for {fid}")
        file = self._exec_path(state, request).joinpath(fid)
        until = helper.now() + datetime.timedelta(
            seconds=state["settings"].WAIT_FOR_JOB)
        while helper.now() < until:
            logger.debug(f"waiting for {fid}")
            if file.exists():
                break
            await asyncio.sleep(0.5)
        if not file.exists():
            raise FileNotFoundError(f"{fid} not found")
        result = ExecutionResult(file)

        async def event_generator():
            first = None
            async for line in result.follow():
                t1, _, event, payload = line.rstrip().split(" ", 3)
                if not first:
                    first = fromisoformat(t1)
                last = t1
                runtime = (fromisoformat(t1) - first).total_seconds()
                if event in (EVENT_STDERR, EVENT_STDOUT):
                    payload = payload.replace("\\n", "\n")
                    payload = ''.join(c for c in payload if c.isprintable())
                yield f"event: {event}\ndata: {runtime} {payload}\n\n"
            yield f"event: eof\ndata:\n\n"

        return Stream(event_generator(), media_type="text/event-stream")
