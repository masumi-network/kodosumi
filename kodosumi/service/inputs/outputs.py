import asyncio
import sqlite3
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional

import litestar
import ray
from litestar import Request, delete, get
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Response, ServerSentEvent, Template
from litestar.types import SSEData

import kodosumi
from kodosumi import dtypes
from kodosumi.const import *
from kodosumi.helper import now, serialize
from kodosumi.log import logger
from kodosumi.runner.formatter import DefaultFormatter, Formatter
from kodosumi.runner.main import kill_runner
from kodosumi.service.store import connect


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
                await asyncio.sleep(0)
            if status in STATUS_FINAL:
                if last:
                    if now() - last > AFTER:
                        break
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
            await asyncio.sleep(0)
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
    fid = meta.get("fid", None)
    query = """
        SELECT kind, message 
        FROM monitor 
        WHERE kind IN (?, ?)
        ORDER BY timestamp ASC
    """
    cursor.execute(query, (EVENT_LOCK, EVENT_LEASE))
    locks = set()
    for kind, msg in cursor.fetchall():
        d = dtypes.DynamicModel.model_validate_json(msg)
        lid = d.root["dict"]["lid"]
        if kind == EVENT_LOCK:
            locks.add(lid)
        else:
            locks.remove(lid)
        await asyncio.sleep(0.05)
    if status not in STATUS_FINAL and locks:
        status = STATUS_AWAITING
    response = {
        "status": status,
        "timestamp": last_timestamp,
        "final": final,
        "fid": fid,
        "summary": meta.get("summary"),
        "description": meta.get("description"),
        "tags": meta.get("tags"),
        "deprecated": meta.get("deprecated"),
        "author": meta.get("author"),
        "organization": meta.get("organization"),
        "version": meta.get("version"),
        "kodosumi_version": meta.get("kodosumi_version"),
        "app_url": meta.get("app_url"),
        "entry_point": meta.get("entry_point"),
        "username": meta.get("username"),
        "locks": locks
    }
    conn.close()
    return response

class OutputsController(litestar.Controller):

    tags = ["Execution Control"]

    @get("/status/{fid:str}", summary="Get Execution Status",
          description="Retrieve the status of an execution including the final result if available and existing locks.", operation_id="50_get_status")
    async def get_status(self, 
                         fid: str, 
                         state: State,
                         request: Request,
                         extended: bool=False) -> Dict:
        while True:
            conn, _ = await connect(fid, request.user, state, extended)
            if not conn:
                raise NotFoundException(f"Execution {fid} not found.")
            ret =  await _status(conn)
            if ret["status"]:
                return ret
            await asyncio.sleep(SLEEP)

    # todo: move to admin panel
    @get("/status/view/{fid:str}", include_in_schema=False)
    async def view_status(self, fid: str) -> Template:
        return Template(STATUS_TEMPLATE, context={"fid": fid})

    @delete("/{fid:str}", summary="Delete or Kill Execution",
         description="Kills an active deletes a completed execution.", operation_id="51_delete_execution")
    async def delete_execution(
            self, 
            fid: str, 
            request: Request, 
            state: State) -> None:
        conn, db_file = await connect(fid, request.user, state, False)
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

    @get("/stream/{fid:str}", summary="Stream Execution Events",
          description="Full Event Stream of an execution.", operation_id="52_get_stream")
    async def get_stream(self, 
                         fid: str, 
                         request: Request, 
                         state: State,
                         extended: bool=False) -> ServerSentEvent:
        return await self._stream(fid, state, request, filter_events=None,
                                  formatter=None, extended=extended)

    @get("/main/{fid:str}", summary="Stream Main Execution Events",
          description="Stream the main events of an execution including the meta data, user input, agent information, status, errors, action, results, final results, locks and leases.", operation_id="53_get_main_stream")
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
            fid, state, request, filter_events=MAIN_EVENTS, 
            formatter=formatter, extended=extended)

    @get("/stdio/{fid:str}", summary="Stream Standard IO Execution Events",
          description="Stream the standard IO events including `STDOUT`, `STDERR`, debug messages, errors and file upload information.", operation_id="54_get_stdio_stream")
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
        conn, db_file = await connect(fid, request.user, state, extended)        
        return ServerSentEvent(_event(fid, conn, filter_events, formatter))
 
    @delete("/", summary="Delete or Kill list of Executions",
         description="Kills active and deletes selected executions.", operation_id="55_delete_list")
    async def delete_list(
            self, 
            request: Request, 
            state: State) -> None:
        js = await request.json()
        for fid in js.get("fid", []):
            conn, db_file = await connect(fid, request.user, state, False)
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
                dtypes.Markdown(body="no result, yet. please be patient."))
        conn.close()
        runtime = last - first if last and first else None
        return {
            "fid": fid,
            "kind": "final",
            "raw": result,
            "timestamp": first,
            "runtime": runtime,
            "meta": dtypes.DynamicModel.model_validate_json(
                meta).model_dump().get("dict", {}),
            "version": kodosumi.__version__
        }

    @get("/html/{fid:str}", summary="Render HTML of Final Result",
         description="Render Final Result in HTML.", operation_id="56_final_html")
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
         description="Render Final Result in raw format.", operation_id="57_final_raw")
    async def final_raw(
            self, 
            fid: str,
            request: Request, 
            state: State) -> Response:
        ret = await self._get_final(fid, request, state)
        return Response(content=ret["raw"])
