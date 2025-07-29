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