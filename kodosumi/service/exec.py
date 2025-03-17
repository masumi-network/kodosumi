import asyncio
import sqlite3
import markdown
from pathlib import Path
from typing import AsyncGenerator, Optional, Union
from ansi2html import Ansi2HTMLConverter
import yaml
import litestar
from litestar.response import ServerSentEvent
from litestar import Request, get
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from litestar.response import Response, Stream
from litestar.types import SSEData

from kodosumi.dtypes import DynamicModel, Execution
from kodosumi.helper import now
from kodosumi.log import logger
from kodosumi.runner import EVENT_STATUS, STATUS_FINAL
from kodosumi.spooler import DB_FILE

DEFAULT_FORMATTER = {
    "action": None, # class specific rendering of tool actions
    "result": None, # class specific rendering of tool results
    "final": None, # class specific rendering of final results
}

class Formatter: 

    def convert(self, kind: str, message: str) -> str:
        raise NotImplementedError()
    

class DefaultFormatter(Formatter):

    def __init__(self):
        self.ansi = Ansi2HTMLConverter()

    def md(self, text: str) -> str:
        return markdown.markdown(text)

    def dict2yaml(self, message: str) -> str:
        model = DynamicModel.model_validate_json(message)
        return yaml.safe_dump(model.model_dump(), allow_unicode=True)

    def ansi2html(self, message: str) -> str:
        return self.ansi.convert(message, full=False)

    def AgentFinish(self, values) -> str:
        ret = ['<div class="info-l1">Agent Finish</div>']
        if values.get("thought", None):
            ret.append(
                f'<div class="info-l2">Thought</div>"' + self.md(
                    values['thought']))
        if values.get("text", None):
            ret.append(self.md(values['text']))
        elif values.get("output", None):
            ret.append(self.md(values['output']))
        return "\n".join(ret)

    def TaskOutput(self, values) -> str:
        ret = ['<div class="info-l1">Task Output</div>']
        agent = values.get("agent", "unnamed agent")
        if values.get("name", None):
            ret.append(
                f'<div class="info-l2">{values["name"]} ({agent})</div>')
        else:
            ret.append(f'<div class="info-l2">{agent}</div>')
        if values.get("description", None):
            ret.append(
                f'<div class="info-l3">Task Description: </div>'
                f'<em>{values["description"]}</em>')
        if values.get("raw", None):
            ret.append(self.md(values['raw']))
        return "\n".join(ret)

    def CrewOutput(self, values) -> str:
        ret = ['<div class="info-l1">Crew Output</div>']
        if values.get("raw", None):
            ret.append(self.md(values['raw']))
        else:
            ret.append("no output found")
        return "\n".join(ret)

    def obj2html(self, message: str) -> str:
        model = DynamicModel.model_validate_json(message)
        ret = []
        for elem, values in model.root.items():
            meth = getattr(self, elem, None)
            if meth:
                ret.append(meth(values))
            else:
                ret.append(f'<div class="info-l1">{elem}</div>')
                ret.append(f"<pre>{values}</pre>")
        return "\n".join(ret)

    def convert(self, kind: str, message: str) -> str:
        if kind == "inputs":
            return self.dict2yaml(message)
        if kind in ("stdout", "stderr"):
            return self.ansi2html(message)
        if kind in ("action", "result", "final"):
            return self.obj2html(message)
        return message


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
            inputs = DynamicModel(**model.root).model_dump_json()[:80]
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
    await asyncio.sleep(0.25)
    while follow:
        db_file = follow.pop(0)
        result = await _query(db_file, state, with_final)
        if result.status not in STATUS_FINAL:
            follow.append(db_file)
        yield f"UPDATE: {result.model_dump_json()}\n\n"
        await asyncio.sleep(0.25)


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
    try:
        t0 = now()
        while True:
            cursor.execute("""
                SELECT id, timestamp, kind, message 
                FROM monitor 
                WHERE id > ?
                ORDER BY timestamp ASC
            """, (offset, ))
            for _id, stamp, kind, msg in cursor.fetchall():
                t0 = now()
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
                    # yield f"{kind}: {timestamp}:{message}\n\n"
                offset = _id
            if status in STATUS_FINAL:
                break
            await asyncio.sleep(0.25)
            if now() > t0 + 1:
                t0 = now()
                logger.debug("looping")
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

    try:
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
            logger.info(f"show page: {p}")
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

    finally:
        logger.info("done streaming, finally closed")


class ExecutionControl(litestar.Controller):

    @get("/")
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
            _follow(state, listing, start, end), 
            media_type="text/plain"
        )

    @get("/{fid:str}")
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
                #logger.info(f"{fid} - not found")
            await asyncio.sleep(0.25)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.warning(f"{fid} - found after {now() - t0:.2f}s")
        listing = [db_file]
        return Stream(
            _follow(state, listing, with_final=True), 
            media_type="text/plain"
        )

    @get("/state/{fid:str}")
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
            await asyncio.sleep(0.25)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.warning(f"{fid} - found after {now() - t0:.2f}s")
        return await _query(db_file, state, with_final=True)

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
            await asyncio.sleep(0.25)
            if now() > t0 + waitfor:
                raise NotFoundException(
                    f"Execution {fid} not found after {waitfor}s.")
        if loop:
            logger.warning(f"{fid} - found after {now() - t0:.2f}s")
        return ServerSentEvent(
            _event(db_file, filter_events, formatter), 
        )

    @get("/out/{fid:str}")
    async def execution_stdout(
            self, fid: str, request: Request, state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request, ("stdout", ))

    @get("/err/{fid:str}")
    async def execution_stderr(
            self, fid: str, request: Request, state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request, ("stderr", ))

    @get("/event/{fid:str}")
    async def execution_event(
            self, fid: str, request: Request, state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request)    
    
    @get("/format/{fid:str}")
    async def execution_format(
            self, fid: str, request: Request, state: State) -> ServerSentEvent:
        return await self._stream(fid, state, request, filter_events=None,
                                  formatter=DefaultFormatter())

    @get("/stream")
    async def execution_stream(
            self, 
            request: Request, 
            state: State,
            p: int=0,
            pp: int=10) -> ServerSentEvent:
        return ServerSentEvent(_listing(state, request, p, pp))
