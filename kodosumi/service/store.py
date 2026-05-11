from kodosumi import helper
from kodosumi.helper import now
from kodosumi.log import logger
from kodosumi.const import DB_FILE, SLEEP


from litestar.datastructures import State
from litestar.exceptions import NotFoundException


import asyncio
import sqlite3
from pathlib import Path
from typing import Tuple

SHORT_WAIT = 1


async def connect(fid: str,
                   user: str,
                   state: State,
                   extended: bool) -> Tuple[sqlite3.Connection, Path]:
    """Connect to the execution event store for reading.

    When EXECUTION_DATABASE is set, uses PostgreSQL via the
    PostgresExecutionReader stored in app state. Otherwise falls
    back to per-user SQLite files (original behavior).
    """
    exec_reader = state.get("exec_reader")
    if exec_reader is not None:
        return await _connect_postgres(fid, user, state, extended, exec_reader)
    return await _connect_sqlite(fid, user, state, extended)


async def _connect_sqlite(fid: str, user: str, state: State,
                          extended: bool) -> Tuple[sqlite3.Connection, Path]:
    """Original SQLite connection logic."""
    db_file = Path(state["settings"].EXEC_DIR).joinpath(
        user, fid, DB_FILE)
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


async def _connect_postgres(fid: str, user: str, state: State,
                            extended: bool, reader) -> Tuple:
    """PostgreSQL connection via the centralized execution reader.

    Returns a (reader, fid) tuple that callers can use to read events.
    The reader handles connection pooling internally.
    """
    waitfor = state["settings"].WAIT_FOR_JOB if extended else SHORT_WAIT
    t0 = helper.now()
    while True:
        events = await reader.read_events(fid, after_id=0, kinds=())
        if events:
            break
        await asyncio.sleep(SLEEP)
        if helper.now() > t0 + waitfor:
            raise NotFoundException(
                f"Execution {fid} not found after {waitfor}s.")
    logger.debug(f"{fid} - found in postgres after {now() - t0:.2f}s")
    return (reader, fid)