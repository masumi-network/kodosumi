from pathlib import Path
from typing import AsyncGenerator

import litestar
from litestar import MediaType, Request, get
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Response, Stream, Template

from kodosumi import helper
from kodosumi.config import InternalSettings
from kodosumi.log import logger
from kodosumi.runner import EVENT_FINAL, EVENT_RESULT
from kodosumi.service.result import ExecutionResult


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
        executions = []
        if exec_path.exists() and exec_path.is_dir():
            for exec_dir in exec_path.iterdir():
                if not exec_dir.is_dir():
                    continue
                result = ExecutionResult(exec_dir)
                #if not result.load():
                #    async for _ in result.follow(): pass
                #    result.dump()
                status = await result.read_state(None)
                executions.append(status)
            executions.sort(key=lambda x: x['tearup'], reverse=True)
        logger.info(f"GET /-/executions in {helper.now() - t0}")
        if helper.wants(request, MediaType.HTML):
            return Template("executions.html", 
                            context={"executions": executions})
        return Response(content=executions)

    @get("/-/executions/{fid:str}")
    async def execution_detail(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Response:
        
        t0 = helper.now()
        logger.info(f"GET /-/executions/{fid} - open")
        file = self._exec_path(state, request).joinpath(fid)
        result = ExecutionResult(file)

        timeout = state["settings"].WAIT_FOR_JOB
        async for _, _, event, payload in result.follow(timeout, quick=True): 
            pass

        execution = result.get_state()
        execution["alive"] = await result.is_alive()

        d = (helper.now() - t0).total_seconds()
        logger.info(f"GET /-/executions/{fid} - closed in {d}")

        execution["fid"] = fid
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
        resp = await self._get_results(fid, state, request)
        d = (helper.now() - t0).total_seconds()
        logger.info(f"GET /-/executions/final/{fid} in {d}")
        return Response(content=resp["final"])

    @get("/-/executions/results/{fid:str}")
    async def execution_results(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Response:

        t0 = helper.now()
        resp = await self._get_results(fid, state, request)
        d = (helper.now() - t0).total_seconds()
        logger.info(f"GET /-/executions/results/{fid} in {d}")
        return Response(content=resp)

    async def _get_results(self, 
                           fid: str, 
                           state: State, 
                           request: Request) -> dict:

        file = self._exec_path(state, request).joinpath(fid)
        if not file.exists():
            raise NotFoundException(f"{fid} not found")

        result = ExecutionResult(file)
        items = []
        final = None

        async for _, _, event, payload in result.follow():
            if event == EVENT_RESULT:
                items.append(payload)
            elif event == EVENT_FINAL:
                final = payload
        resp = result.get_state()
        resp.update({"result": items, "final": final})
        return resp

    @get("/-/executions/follow/{fid:str}")
    async def follow(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Stream:

        t0 = helper.now()
        logger.info(f"GET /-/executions/follow/{fid} - open")
        file = self._exec_path(state, request).joinpath(fid)
        result = ExecutionResult(file)

        async def _follow() -> AsyncGenerator[str, None]:
            async for timestamp, runtime, event, payload in result.follow():
                yield f"{timestamp} {runtime} - {event}: {payload}\n"
            d = (helper.now() - t0).total_seconds()
            logger.info(f"GET /-/executions/follow/{fid} - closed in {d}")

        return Stream(_follow(), media_type="text/plain")

    @get("/-/executions/sse/{fid:str}")
    async def follow_sse(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Stream:

        t0 = helper.now()
        logger.info(f"GET /-/executions/sse/{fid} - open")
        file = self._exec_path(state, request).joinpath(fid)
        result = ExecutionResult(file)

        timeout = state["settings"].WAIT_FOR_JOB
        async def _follow() -> AsyncGenerator[str, None]:
            async for ts, runtime, event, payload in result.follow(timeout):
                if event == "error":
                    event = "except"
                yield f"event: {event}\ndata: {ts} {runtime} {payload}\n\n"
            yield f"event: eof\ndata:\n\n"
            d = (helper.now() - t0).total_seconds()
            logger.info(f"GET /-/executions/sse/{fid} - closed in {d}")

        return Stream(_follow(), media_type="text/event-stream")

