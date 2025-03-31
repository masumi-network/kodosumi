import asyncio
import shutil
import sqlite3
from pathlib import Path
from typing import AsyncGenerator, Optional, Union

import litestar
import ray
from litestar import Request, delete, get
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Response, ServerSentEvent, Stream, Template
from litestar.types import SSEData

import kodosumi.core
from kodosumi.dtypes import DynamicModel, Execution, Markdown
from kodosumi.helper import now, serialize
from kodosumi.log import logger
from kodosumi.runner.const import EVENT_STATUS, NAMESPACE, STATUS_FINAL
from kodosumi.runner.main import kill_runner
from kodosumi.runner.const import DB_FILE
from kodosumi.runner.formatter import DefaultFormatter, Formatter


SLEEP = 0.25

async def _query(
        db_file: Path, state: State, with_final: bool=False) -> Execution:
    conn = sqlite3.connect(str(db_file), isolation_level=None)
    conn.execute('pragma journal_mode=wal;')
    conn.execute('pragma synchronous=normal;')
    conn.execute('pragma read_uncommitted=true;')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT kind, message FROM monitor 
        WHERE kind IN ('meta', 'status', 'inputs', 'final', 'error')
        ORDER BY timestamp ASC
    """)
    status = None
    inputs = None
    error = []
    final = None
    fields = {
        "fid": db_file.parent.name,
        "summary": None,
        "description": None,
        "author": None,
        "organization": None,
    }
    for kind, message in cursor.fetchall():
        if kind == "meta":
            model = DynamicModel.model_validate_json(message)
            for field in fields:
                if field in model.root["dict"]:
                    fields[field] = model.root["dict"].get(field)
        elif kind == "status":
            status = message
        elif kind == "error":
            error.append(message)
        elif kind == "inputs":
            model = DynamicModel.model_validate_json(message)
            inputs = DynamicModel(**model.root).model_dump_json()
        elif kind == "final" and with_final:
            model = DynamicModel.model_validate_json(message)
            final = DynamicModel(**model.root).model_dump()
    cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM monitor")
    first, last = cursor.fetchone()
    conn.close()
    runtime = last - first if last and first else None
    return Execution(**fields,  # type: ignore
        status=status, started_at=first, last_update=last, 
        inputs=inputs, runtime=runtime, final=final, error=error or None)


async def _follow(state, 
                  listing, 
                  start: Optional[int]=None, 
                  end: Optional[int]=None,
                  with_final: bool=False) -> AsyncGenerator[str, None]:
    follow = []
    start = start if start else 0
    end = end if end else len(listing)
    for db_file in listing[start:end]:
        result = await _query(db_file, state, with_final=with_final)
        if result.status not in STATUS_FINAL:
            follow.append(db_file)
        yield f"{result.model_dump_json()}\n"
    await asyncio.sleep(SLEEP)
    while follow:
        db_file = follow.pop(0)
        result = await _query(db_file, state, with_final)
        if result.status not in STATUS_FINAL:
            follow.append(db_file)
        yield f"UPDATE: {result.model_dump_json()}\n\n"
        await asyncio.sleep(SLEEP)


async def _event(
        db_file: Path, 
        filter_events=None,
        formatter:Optional[Formatter]=None) -> AsyncGenerator[SSEData, None]:
    status = None
    offset = 0
    conn = sqlite3.connect(str(db_file), isolation_level=None)
    conn.execute('pragma journal_mode=wal;')
    conn.execute('pragma synchronous=normal;')
    conn.execute('pragma read_uncommitted=true;')
    cursor = conn.cursor()

    cursor.execute("""
        SELECT message FROM monitor WHERE kind = 'status'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row:
        status = row[0]
        if status not in STATUS_FINAL:
            try:
                ray.get_actor(db_file.parent.name, namespace=NAMESPACE)
            except ValueError:
                cursor.execute("""
                    INSERT INTO monitor (timestamp, kind, message) 
                    VALUES (?, 'error', 'actor not found')
                """, (now(),))
                cursor.execute("""
                    INSERT INTO monitor (timestamp, kind, message) 
                    VALUES (?, 'status', 'error')
                """, (now(),))
    try:
        t0 = now()
        lst = None
        while True:
            cursor.execute("""
                SELECT id, timestamp, kind, message 
                FROM monitor 
                WHERE id > ?
                ORDER BY timestamp ASC
            """, (offset, ))
            n = 0
            for _id, stamp, kind, msg in cursor.fetchall():
                t0 = now()
                lst = t0
                if kind == EVENT_STATUS:
                    status = msg
                if filter_events is None or kind in filter_events:
                    out = f"{stamp}:"
                    out += formatter.convert(kind, msg) if formatter else msg
                    yield {
                        "event": kind,
                        "id": _id,
                        "data": out
                    }
                offset = _id
                n += 1
            if status in STATUS_FINAL and lst and lst + 10 < now():
                break
            await asyncio.sleep(SLEEP)
            if now() > t0 + 1:
                t0 = now()
                yield {
                    "id": 0,
                    "event": "alive",
                    "data": f"{now()}:alive"
                }
        yield {
            "id": 0,
            "event": "eof",
            "data": ""
        }
    finally:
        conn.close()


async def _listing(state: State, 
                   request: Request, 
                   p: int, 
                   pp: int) -> AsyncGenerator[SSEData, None]:
    
    exec_dir = Path(state["settings"].EXEC_DIR).joinpath(request.user)
    previous_state:dict = {} 
    
    initial = True
    while True:
        await asyncio.sleep(1.)

        if not exec_dir.exists():
            continue

        listing = []
        for db_file in exec_dir.iterdir():
            db_file = db_file.joinpath(DB_FILE)
            if not db_file.is_file():
                continue
            listing.append(db_file)
        if not listing:
            continue

        listing.sort(reverse=True)
        total_pages = (len(listing) + pp - 1) // pp
        if p < 0:
            p = 0
        if p >= total_pages:
            p = total_pages - 1

        start = p * pp
        end = start + pp
        current_state = {}  
        event_triggered = False

        if initial:
            yield {
                "id": now(),
                "event": "info",
                "data": DynamicModel({
                    "total": len(listing),
                    "page": p,
                    "pp": pp,
                    "total_pages": total_pages
                }).model_dump_json()
            }

        for db_file in listing[start:end]:
            result = await _query(db_file, state)
            current_state[result.fid] = result.status

            if result.fid in previous_state:
                if previous_state[result.fid] not in STATUS_FINAL:
                    yield {
                        "id": now(),
                        "event": "update",
                        "data": result.model_dump_json()
                    }
                    event_triggered = True
            else:
                yield {
                    "id": now(),
                    "event": "append" if initial else "prepend",
                    "data": result.model_dump_json()
                }
                event_triggered = True

        for fid in previous_state.keys():
            if fid not in current_state:
                yield {
                    "id": now(),
                    "event": "delete",
                    "data": fid
                }
                event_triggered = True

        if not event_triggered:
            yield {
                "id": now(),
                "event": "alive",
                "data": "No updates or deletes"
            }

        previous_state = current_state
        initial = False


class ExecutionControl(litestar.Controller):

    tags = ["Execution Control"]

    @get("/", summary="List Executions",
         description="List all executions for the current user.")
    async def list_executions(
            self, 
            request: Request, 
            state: State,
            p: int=0,
            pp: int=10) -> Union[Stream, Response]:
        exec_dir = Path(state["settings"].EXEC_DIR).joinpath(request.user)
        if not exec_dir.exists():
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
            return Response(
                content="No executions found.", media_type="text/plain")
        start = p * pp
        end = start + pp
        return Stream(
            _follow(state, listing, start, end), media_type="text/plain")

    @get("/{fid:str}", include_in_schema=False)
    async def execution_detail(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Union[Stream, Response]:
        db_file = Path(state["settings"].EXEC_DIR).joinpath(
            request.user, fid, DB_FILE)
        t0 = now()
        loop = False
        waitfor = state["settings"].WAIT_FOR_JOB
        while not db_file.exists():
            if not loop:
                loop = True
            await asyncio.sleep(SLEEP)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.debug(f"{fid} - found after {now() - t0:.2f}s")
        listing = [db_file]
        return Stream(
            _follow(state, listing, with_final=True), media_type="text/plain")

    @get("/state/{fid:str}", summary="Execution State",
         description="Retrieve execution state.")
    async def execution_state(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Execution:
        db_file = Path(state["settings"].EXEC_DIR).joinpath(
            request.user, fid, DB_FILE)
        t0 = now()
        loop = False
        waitfor = state["settings"].WAIT_FOR_JOB
        while not db_file.exists():
            if not loop:
                loop = True
            await asyncio.sleep(SLEEP)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.debug(f"{fid} - found after {now() - t0:.2f}s")
        return await _query(db_file, state, with_final=True)

    async def _get_final(
            self, 
            fid: str,
            request: Request, 
            state: State) -> dict:
        db_file = Path(state["settings"].EXEC_DIR).joinpath(
            request.user, fid, DB_FILE)
        t0 = now()
        loop = False
        waitfor = state["settings"].WAIT_FOR_JOB
        while not db_file.exists():
            if not loop:
                loop = True
            await asyncio.sleep(SLEEP)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.debug(f"{fid} - found after {now() - t0:.2f}s")
        conn = sqlite3.connect(str(db_file), isolation_level=None)
        conn.execute('pragma journal_mode=wal;')
        conn.execute('pragma synchronous=normal;')
        conn.execute('pragma read_uncommitted=true;')
        cursor = conn.cursor()
        cursor.execute("SELECT message FROM monitor WHERE kind = 'meta'")
        row = cursor.fetchone()
        if row:
            meta, = row
        else:
            meta = {}
        cursor.execute("SELECT MIN(timestamp), MAX(timestamp) FROM monitor")
        first, last = cursor.fetchone()
        cursor.execute("SELECT message FROM monitor WHERE kind = 'final'")
        row = cursor.fetchone()
        if row:
            result, = row
        else:
            result = serialize(
                Markdown(body="no result, yet. please be patient."))
        conn.close()
        runtime = last - first if last and first else None
        return {
            "fid": fid,
            "kind": "final",
            "raw": result,
            "timestamp": first,
            "runtime": runtime,
            "meta": DynamicModel.model_validate_json(
                meta).model_dump().get("dict", {}),
            "version": kodosumi.core.__version__
        }

    @get("/html/{fid:str}", summary="Render HTML of Final Result",
         description="Render Final Result in HTML.")
    async def final_html(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Template:
        formatter = DefaultFormatter()
        ret = await self._get_final(fid, request, state)
        ret["main"] = formatter.convert(ret["kind"], ret["raw"])
        return Template("final.html", context=ret)

    @get("/raw/{fid:str}", summary="Render Raw of Final Result",
         description="Render Final Result in raw format.")
    async def final_raw(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Response:
        ret = await self._get_final(fid, request, state)
        return Response(content=ret["raw"])


    async def _stream(self, 
                      fid, 
                      state: State, 
                      request: Request,
                      filter_events=None,
                      formatter=None) -> ServerSentEvent:
        db_file = Path(state["settings"].EXEC_DIR).joinpath(
            request.user, fid, DB_FILE)
        t0 = now()
        loop = False
        waitfor = state["settings"].WAIT_FOR_JOB
        while not db_file.exists():
            if not loop:
                loop = True
            await asyncio.sleep(SLEEP)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.debug(f"{fid} - found after {now() - t0:.2f}s")
        return ServerSentEvent(_event(db_file, filter_events, formatter))

    @get("/out/{fid:str}", summary="STDOUT Stream",
         description="STDOUT stream as Server Send Events (SSE).")
    async def execution_stdout(
            self, fid: str, request: Request, state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request, ("stdout", ))

    @get("/err/{fid:str}", summary="STDERR Stream",
         description="STDERR stream as Server Send Events (SSE).")
    async def execution_stderr(
            self, fid: str, request: Request, state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request, ("stderr", ))

    @get("/event/{fid:str}", summary="Execution Event Stream",
         description="Event Stream as Server Send Events (SSE).")
    async def execution_event(
            self, fid: str, request: Request, state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request)    
    
    @get("/format/{fid:str}", include_in_schema=False)
    async def execution_format(
            self, fid: str, request: Request, state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request, filter_events=None,
                                  formatter=DefaultFormatter())

    @get("/stream", summary="Stream Executions",
         description="Pagination stream as Server Send Events (SSE).")
    async def execution_stream(
            self, 
            request: Request, 
            state: State,
            p: int=0,
            pp: int=10) -> ServerSentEvent:
        return ServerSentEvent(_listing(state, request, p, pp))

    @delete("/{fid:str}", summary="Delete or Kill Execution",
         description="Kills (if active) or deletes (if finished) an execution.")
    async def delete_execution(
            self, 
            fid: str, 
            request: Request, 
            state: State) -> None:
        db_file = Path(state["settings"].EXEC_DIR).joinpath(
            request.user, fid, DB_FILE)
        if not db_file.exists():
            raise NotFoundException(fid)
        job = await _query(db_file, state, with_final=False)
        if job.status not in STATUS_FINAL:
            try:
                kill_runner(job.fid)
            except:
                logger.critical(f"failed to kill {fid}", exc_info=True)
            else:
                logger.warning(f"killed {fid}")
        if db_file.parent.exists():
            shutil.rmtree(str(db_file.parent))
            logger.warning(f"deleted {fid}")
        else:
            logger.warning(f"{fid} not found")