import asyncio
import sqlite3
from pathlib import Path
from typing import AsyncGenerator, Optional, Union

import litestar
from litestar import Request, get
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Response, Stream

from kodosumi.dtypes import DynamicModel, Execution
from kodosumi.endpoint import find_endpoint
from kodosumi.helper import now
from kodosumi.log import logger
from kodosumi.runner import STATUS_FINAL
from kodosumi.spooler import DB_FILE


async def _query(
        db_file: Path, state: State, with_final: bool=False) -> Execution:
    conn = sqlite3.connect(str(db_file), isolation_level=None)
    conn.execute('pragma journal_mode=wal;')
    conn.execute('pragma synchronous=normal;')
    conn.execute('pragma read_uncommitted=true;')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT kind, message FROM monitor 
        WHERE kind IN ('meta', 'status', 'inputs', 'final')
        ORDER BY timestamp ASC
    """)
    status = None
    inputs = None
    summary = None
    description = None
    author = None
    organization = None
    final = None
    fid = db_file.parent.name
    for kind, message in cursor.fetchall():
        if kind == "meta":
            model = DynamicModel.model_validate_json(message)
            base_url = model.root["dict"].get("base_url")
            fid = model.root["dict"].get("fid")
            endpoint = find_endpoint(state, base_url)
            if endpoint:
                summary = endpoint.summary
                description = endpoint.description
                author = endpoint.author
                organization = endpoint.organization
        elif kind == "status":
            status = message
        elif kind == "inputs":
            model = DynamicModel.model_validate_json(message)
            inputs = DynamicModel(**model.root["dict"]).model_dump_json()[:80]
        elif kind == "final" and with_final:
            model = DynamicModel.model_validate_json(message)
            final = DynamicModel(**model.root).model_dump()
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM monitor")
    first, last = cursor.fetchone()
    conn.close()
    runtime = last - first if last and first else None
    return Execution(
        fid=fid, status=status, started_at=first, last_update=last, 
        inputs=inputs, summary=summary, description=description, 
        author=author, organization=organization, runtime=runtime,
        final=final)

async def _follow(base, 
                  state, 
                  listing, 
                  start: Optional[int]=None, 
                  end: Optional[int]=None,
                  with_final: bool=False) -> AsyncGenerator[str, None]:
    logger.info(f"{base} - start streaming")
    follow = []
    start = start if start else 0
    end = end if end else len(listing)
    for db_file in listing[start:end]:
        result = await _query(db_file, state, with_final=with_final)
        if result.status not in STATUS_FINAL:
            follow.append(db_file)
        yield f"{result.model_dump_json()}\n"
    await asyncio.sleep(0.25)
    while follow:
        db_file = follow.pop(0)
        result = await _query(db_file, state, with_final)
        if result.status not in STATUS_FINAL:
            follow.append(db_file)
        yield f"UPDATE: {result.model_dump_json()}\n\n"
        await asyncio.sleep(0.25)
    logger.info(f"{base} - streaming closed")


async def _stream(base: str,
                  event: str, 
                  db_file: Path) -> AsyncGenerator[str, None]:
    logger.info(f"{base} - start streaming")
    status = None
    offset = 0
    conn = sqlite3.connect(str(db_file), isolation_level=None)
    conn.execute('pragma journal_mode=wal;')
    conn.execute('pragma synchronous=normal;')
    conn.execute('pragma read_uncommitted=true;')
    cursor = conn.cursor()
    while True:
        cursor.execute("""
            SELECT id, timestamp, kind, message 
            FROM monitor 
            WHERE kind IN (?, 'status') AND id > ?
            ORDER BY timestamp ASC
        """, (event, offset))
        for _id, timestamp, kind, message in cursor.fetchall():
            if kind == "status":
                status = message
            else:
                yield f"{message}\n"
            offset = _id
        if status in STATUS_FINAL:
            break
        await asyncio.sleep(0.25)
    conn.close()
    logger.info(f"{base} - streaming closed")


class ExecutionControl(litestar.Controller):

    @get("/exec")
    async def list_executions(
            self, 
            request: Request, 
            state: State,
            p: int=0,
            pp: int=10) -> Union[Stream, Response]:
        base = f"GET /-/exec"
        exec_dir = Path(state["settings"].EXEC_DIR).joinpath(request.user)
        if not exec_dir.exists():
            logger.info(f"{base} - not found")
            return Response(
                content="No executions found.", media_type="text/plain")
        listing = []
        for db_file in exec_dir.iterdir():
            db_file = db_file.joinpath(DB_FILE)
            if not (db_file.exists() and db_file.is_file()):
                continue
            listing.append(db_file)
        listing.sort(reverse=True)
        if not listing:
            logger.info(f"{base} - empty")
            return Response(
                content="No executions found.", media_type="text/plain")
        start = p * pp
        end = start + pp
        return Stream(
            _follow(base, state, listing, start, end), 
            media_type="text/plain"
        )

    @get("/exec/{fid:str}")
    async def execution_detail(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Union[Stream, Response]:
        base = f"GET /-/exec/{fid}"
        db_file = Path(state["settings"].EXEC_DIR).joinpath(
            request.user, fid, DB_FILE)
        t0 = now()
        loop = False
        waitfor = state["settings"].WAIT_FOR_JOB
        while not db_file.exists():
            if not loop:
                loop = True
                #logger.info(f"{fid} - not found")
            await asyncio.sleep(0.25)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.warning(f"{fid} - found after {now() - t0:.2f}s")
        listing = [db_file]
        return Stream(
            _follow(base, state, listing, with_final=True), 
            media_type="text/plain"
        )

    async def _stream(self, event, fid, state: State, request: Request) -> Stream:
        base = f"GET /-/{event}/{fid}"
        db_file = Path(state["settings"].EXEC_DIR).joinpath(
            request.user, fid, DB_FILE)
        t0 = now()
        loop = False
        waitfor = state["settings"].WAIT_FOR_JOB
        while not db_file.exists():
            if not loop:
                loop = True
            await asyncio.sleep(0.25)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.warning(f"{fid} - found after {now() - t0:.2f}s")
        return Stream(
            _stream(base, event, db_file), 
            media_type="text/plain",
            headers={
                "Cache-Control": "no-cache", 
                "Transfer-Encoding": "chunked"
            }
        )

    @get("/out/{fid:str}")
    async def execution_stdout(
            self, fid: str, request: Request, state: State) -> Stream:
        return await self._stream("stdout", fid, state, request)

    @get("/err/{fid:str}")
    async def execution_stderr(
            self, fid: str, request: Request, state: State) -> Stream:
        return await self._stream("stderr", fid, state, request)

    @get("/debug/{fid:str}")
    async def execution_debug(
            self, fid: str, request: Request, state: State) -> Stream:
        return await self._stream("debug", fid, state, request)    