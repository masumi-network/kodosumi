import asyncio
import json
import sqlite3
from pathlib import Path
from typing import AsyncGenerator, Optional, List, Union, Dict, Tuple
from datetime import datetime, timedelta

import litestar
import ray
from httpx import AsyncClient
from litestar import Request, get, post, MediaType, delete
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Redirect, ServerSentEvent, Template
from litestar.types import SSEData

from kodosumi import helper
from kodosumi.helper import now, wants
from kodosumi.log import logger
from kodosumi.runner.const import *
from kodosumi.runner.formatter import DefaultFormatter, Formatter
from kodosumi.runner.main import kill_runner
from kodosumi.service.inputs.forms import Model
from kodosumi.service.proxy import KODOSUMI_BASE, KODOSUMI_USER
from kodosumi import dtypes

STATUS_TEMPLATE = "status/status.html"
SHORT_WAIT = 1
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

async def _connect(fid: str, 
                   request: Request, 
                   state: State,
                   extended: bool) -> Tuple[sqlite3.Connection, Path]:
    db_file = Path(state["settings"].EXEC_DIR).joinpath(
        request.user, fid, DB_FILE)
    waitfor = state["settings"].WAIT_FOR_JOB if extended else SHORT_WAIT
    loop = False
    t0 = helper.now()
    while not db_file.exists():
        if not loop:
            loop = True
        await asyncio.sleep(SLEEP)
        if helper.now() > t0 + waitfor:
            raise NotFoundException(
                f"Execution {fid} not found after {waitfor}s.")
    if loop:
        logger.debug(f"{fid} - found after {now() - t0:.2f}s")
    conn = sqlite3.connect(str(db_file), isolation_level=None)
    conn.execute('pragma journal_mode=wal;')
    conn.execute('pragma synchronous=normal;')
    conn.execute('pragma read_uncommitted=true;')
    return (conn, db_file)

async def _event(
        fid: str,
        conn: sqlite3.Connection, 
        filter_events: Optional[List[str]]=None,
        formatter:Optional[Formatter]=None) -> AsyncGenerator[SSEData, None]:
    status = None
    offset = 0
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
            await _verify_actor(fid, cursor)
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
                last = stamp
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
            if status in STATUS_FINAL:
                if last:
                    if now() - last > AFTER:
                        break
            await asyncio.sleep(SLEEP)
            if now() > t0 + PING:
                t0 = now()
                if t0 > check + CHECK_ALIVE:
                    if status not in STATUS_FINAL:
                        check = t0
                        if await _verify_actor(fid, cursor):
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


async def _status(conn: sqlite3.Connection) -> Dict:
    status = None
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(timestamp) 
        FROM monitor 
        WHERE kind IN ('status', 'final', 'meta', 'alive')
    """)
    last_timestamp = cursor.fetchone()[0]
    cursor.execute("""
        SELECT message 
        FROM monitor 
        WHERE kind = 'status'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row:
        status = row[0]
    cursor.execute("""
        SELECT message 
        FROM monitor 
        WHERE kind = 'final'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    final = row[0] if row else None
    cursor.execute("""
        SELECT message 
        FROM monitor 
        WHERE kind = 'meta'
        ORDER BY timestamp DESC, id DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row:
        meta_data = dtypes.DynamicModel.model_validate_json(row[0])
        meta = meta_data.root.get("dict", {})
    else:
        meta = {}
    response = {
        "status": status,
        "timestamp": last_timestamp,
        "final": final,
        "fid": meta.get("fid"),
        "summary": meta.get("summary"),
        "description": meta.get("description"),
        "tags": meta.get("tags"),
        "deprecated": meta.get("deprecated"),
        "author": meta.get("author"),
        "organization": meta.get("organization"),
        "version": meta.get("version"),
        "kodosumi_version": meta.get("kodosumi_version"),
        "base_url": meta.get("base_url"),
        "entry_point": meta.get("entry_point"),
        "username": meta.get("username")
    }
    conn.close()
    return response

class OutputsController(litestar.Controller):

    tags = ["Admin Panel"]
    include_in_schema = True

    @get("/status/{fid:str}")
    async def get_status(self, 
                         fid: str, 
                         state: State,
                         request: Request,
                         extended: bool=False) -> Union[Template, Dict]:
        while True:
            conn, _ = await _connect(fid, request, state, extended)
            if not conn:
                raise NotFoundException(f"Execution {fid} not found.")
            ret =  await _status(conn)
            if ret["status"]:
                return ret
            await asyncio.sleep(SLEEP)

    @get("/status/view/{fid:str}")
    async def view_status(self, fid: str) -> Template:
        return Template(STATUS_TEMPLATE, context={"fid": fid})

    @delete("/{fid:str}", summary="Delete or Kill Execution",
         description="Kills an active deletes a completed execution.")
    async def delete_execution(
            self, 
            fid: str, 
            request: Request, 
            state: State) -> None:
        conn, db_file = await _connect(fid, request, state, False)
        if not conn:
            raise NotFoundException(f"Execution {fid} not found.")
        job = await _status(conn)
        if job["status"] not in STATUS_FINAL:
            try:
                kill_runner(fid)
            except:
                logger.critical(f"failed to kill {fid}", exc_info=True)
            else:
                logger.warning(f"killed {fid}")
        try:
            newdb = db_file.parent.joinpath(db_file.name + DB_ARCHIVE)
            db_file.rename(newdb)
            newdb.touch()
        except:
            logger.critical(f"failed to archive {fid}", exc_info=True)
        else:
            logger.warning(f"archived {fid}")

    @get("/stream/{fid:str}")
    async def get_stream(self, 
                         fid: str, 
                         request: Request, 
                         state: State,
                         extended: bool=False) -> ServerSentEvent:
        return await self._stream(fid, state, request, filter_events=None,
                                  formatter=None, extended=extended)

    @get("/main/{fid:str}")
    async def get_main_stream(
            self, 
            fid: str, 
            request: Request, 
            state: State,
            extended: bool=True) -> ServerSentEvent: 
        if "raw" in request.query_params:
            formatter = None
        else:
            formatter = DefaultFormatter()
        return await self._stream(
            fid, state, request, filter_events=MAIN_EVENTS, formatter=formatter,
            extended=extended)

    @get("/stdio/{fid:str}")
    async def get_stdio_stream(
            self, 
            fid: str, 
            request: Request, 
            state: State,
            extended: bool=False) -> ServerSentEvent: 
        if "raw" in request.query_params:
            formatter = None
        else:
            formatter = DefaultFormatter()
        return await self._stream(
            fid, state, request, filter_events=STDIO_EVENTS, 
            formatter=formatter, extended=extended)

    async def _stream(self, 
                      fid, 
                      state: State, 
                      request: Request,
                      filter_events=None,
                      formatter=None,
                      extended: bool=False) -> ServerSentEvent:
        conn, db_file = await _connect(fid, request, state, extended)        
        return ServerSentEvent(_event(fid, conn, filter_events, formatter))
 
    @delete("/", summary="Delete or Kill list of Executions",
         description="Kills active and deletes selected executions.")
    async def delete_list(
            self, 
            request: Request, 
            state: State) -> None:
        js = await request.json()
        for fid in js.get("fid", []):
            conn, db_file = await _connect(fid, request, state, False)
            if not conn:
                raise NotFoundException(f"Execution {fid} not found.")
            job = await _status(conn)
            if job["status"] not in STATUS_FINAL:
                try:
                    kill_runner(fid)
                except:
                    logger.critical(f"failed to kill {fid}", exc_info=True)
                else:
                    logger.warning(f"killed {fid}")
            try:
                newdb = db_file.parent.joinpath(db_file.name + DB_ARCHIVE)
                db_file.rename(newdb)
                newdb.touch()
            except:
                logger.critical(f"failed to archive {fid}", exc_info=True)
            else:
                logger.warning(f"archived {fid}")

