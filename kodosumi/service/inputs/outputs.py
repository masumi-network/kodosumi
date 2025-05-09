import asyncio
import json
import sqlite3
from pathlib import Path
from typing import AsyncGenerator, Optional, List

import litestar
import ray
from httpx import AsyncClient
from litestar import Request, get, post
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Redirect, ServerSentEvent, Template
from litestar.types import SSEData

from kodosumi import helper
from kodosumi.helper import now
from kodosumi.log import logger
from kodosumi.runner.const import *
from kodosumi.runner.formatter import DefaultFormatter, Formatter
from kodosumi.service.inputs.forms import Model
from kodosumi.service.proxy import KODOSUMI_BASE, KODOSUMI_USER

STATUS_TEMPLATE = "status/status.html"
SLEEP = 0.4
AFTER = 10
PING = 3.
CHECK_ALIVE = 15

async def _verify_actor(name: str, cursor):
    try:
        ray.get_actor(name, namespace=NAMESPACE)
        return True
    except ValueError:
        cursor.execute("""
            INSERT INTO monitor (timestamp, kind, message) 
            VALUES (?, 'error', 'actor not found')
        """, (now(),))
        cursor.execute("""
            INSERT INTO monitor (timestamp, kind, message) 
            VALUES (?, 'status', 'error')
        """, (now(),))
        return False



async def _event(
        db_file: Path, 
        filter_events: Optional[List[str]]=None,
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
            await _verify_actor(db_file.parent.name, cursor)
    try:
        t0 = last = None
        check = now()
        select = "SELECT id, timestamp, kind, message FROM monitor"
        order = " ORDER BY timestamp ASC"
        kind_filter = ""
        filter_params = []
        if filter_events:
            filters = list(filter_events)
            if EVENT_STATUS not in filters:
                filters.append(EVENT_STATUS)
            placeholders = ','.join('?' * len(filters))
            kind_filter = f" AND kind IN ({placeholders})"
            filter_params.extend(filters)
        t0 = now()
        while True:
            where_part = "WHERE id > ?"
            current_query = f"{select} {where_part}{kind_filter}{order}"
            current_params = [offset] + filter_params
            cursor.execute(current_query, tuple(current_params))
            for _id, stamp, kind, msg in cursor.fetchall():
                t0 = now()
                last = t0
                if kind == EVENT_STATUS:
                    status = msg
                out = f"{stamp}:"
                out += formatter.convert(kind, msg) if formatter else msg
                if filter_events is None or kind in filter_events:
                    yield {
                        "event": kind,
                        "id": _id,
                        "data": out
                    }
                offset = _id
            if status in STATUS_FINAL and last and last + AFTER < now():
                break
            await asyncio.sleep(SLEEP)
            if now() > t0 + PING:
                t0 = now()
                if t0 > check + CHECK_ALIVE:
                    if status not in STATUS_FINAL:
                        check = t0
                        if await _verify_actor(db_file.parent.name, cursor):
                            yield {
                                "id": 0,
                                "event": "alive",
                                "data": f"{t0}:actor and service alive",
                            }
                        continue
                yield {
                    "id": 0,
                    "event": "alive",
                    "data": f"{t0}:service alive"
                }
        yield {
            "id": 0,
            "event": "eof",
            "data": "end of stream"
        }
    finally:
        conn.close()

class OutputsController(litestar.Controller):

    tags = ["Admin Panel"]
    include_in_schema = True

    @get("/status/{fid:str}")
    async def get_status(self, 
                         fid: str, 
                         state: State,
                         request: Request) -> Template:
        return Template(STATUS_TEMPLATE, context={"fid": fid})

    @get("/stream/{fid:str}")
    async def get_stream(self, 
                         fid: str, 
                         request: Request, 
                         state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request, filter_events=None,
                                  formatter=None)

    @get("/main/{fid:str}")
    async def get_main_stream(self, 
                              fid: str, 
                              request: Request, 
                              state: State) -> ServerSentEvent: 
        if "raw" in request.query_params:
            formatter = None
        else:
            formatter = DefaultFormatter()
        return await self._stream(
            fid, state, request, filter_events=MAIN_EVENTS, formatter=formatter)

    @get("/stdio/{fid:str}")
    async def get_stdio_stream(self, 
                               fid: str, 
                               request: Request, 
                               state: State) -> ServerSentEvent: 
        if "raw" in request.query_params:
            formatter = None
        else:
            formatter = DefaultFormatter()
        return await self._stream(
            fid, state, request, filter_events=STDIO_EVENTS, 
            formatter=formatter)

    async def _stream(self, 
                      fid, 
                      state: State, 
                      request: Request,
                      filter_events=None,
                      formatter=None) -> ServerSentEvent:
        db_file = Path(state["settings"].EXEC_DIR).joinpath(
            request.user, fid, DB_FILE)
        t0 = helper.now()
        loop = False
        waitfor = state["settings"].WAIT_FOR_JOB
        while not db_file.exists():
            if not loop:
                loop = True
            await asyncio.sleep(SLEEP)
            if helper.now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.debug(f"{fid} - found after {now() - t0:.2f}s")
        return ServerSentEvent(_event(db_file, filter_events, formatter))
