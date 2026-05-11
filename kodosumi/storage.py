"""Storage abstraction for execution events.

Provides two implementations:
- SqliteSpoolerWriter: Current behavior — per-user SQLite files (default)
- PostgresSpoolerWriter: Centralized PostgreSQL storage (opt-in)

The writer is used by the Spooler to persist events captured from
Runner actors during agent execution.
"""
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Protocol, Tuple, runtime_checkable

from kodosumi.const import DB_FILE
from kodosumi.log import logger


@runtime_checkable
class SpoolerWriter(Protocol):
    """Protocol for writing execution events from the Spooler."""

    def setup(self, username: str, fid: str) -> Any:
        """Initialize storage for a new execution. Returns a connection handle."""
        ...

    def save(self, conn: Any, fid: str, payload: List[Dict]) -> None:
        """Persist a batch of events."""
        ...

    def close(self, conn: Any) -> None:
        """Close the connection handle."""
        ...


class SqliteSpoolerWriter:
    """Current behavior: per-user SQLite files with WAL mode.

    Each execution gets its own SQLite database at:
        {exec_dir}/{username}/{fid}/sqlite3.db
    """

    def __init__(self, exec_dir: Path):
        self.exec_dir = exec_dir

    def setup(self, username: str, fid: str) -> sqlite3.Connection:
        dir_path = self.exec_dir.joinpath(username, fid)
        dir_path.mkdir(parents=True, exist_ok=True)
        db_path = dir_path.joinpath(DB_FILE)
        conn = sqlite3.connect(
            str(db_path), isolation_level=None, autocommit=True)
        conn.execute('pragma journal_mode=wal;')
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monitor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        return conn

    def save(self, conn: sqlite3.Connection, fid: str,
             payload: List[Dict]) -> None:
        if not payload:
            return
        try:
            cursor = conn.cursor()
            for val in payload:
                cursor.execute(
                    "INSERT INTO monitor (timestamp, kind, message) "
                    "VALUES (?, ?, ?)",
                    (val.get("timestamp"), val.get("kind"),
                     val.get("payload")))
                logger.debug(f"saved {val.get('kind')}: {val} for {fid}")
        except Exception:
            logger.critical(f"failed to save {fid}", exc_info=True)

    def close(self, conn: sqlite3.Connection) -> None:
        conn.close()


class PostgresSpoolerWriter:
    """Centralized PostgreSQL writer for execution events.

    All executions share a single 'execution_events' table with
    fid and username columns for multi-tenant isolation.

    Uses psycopg (sync driver) because the Spooler's save() is
    called from a synchronous context.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._conn = None

    def _get_connection(self):
        if self._conn is None or self._conn.closed:
            try:
                import psycopg
            except ImportError:
                raise ImportError(
                    "psycopg is required for PostgreSQL support. "
                    "Install with: pip install kodosumi[postgres]")
            dsn = self.database_url
            if dsn.startswith("postgresql+asyncpg://"):
                dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
            elif dsn.startswith("postgresql+psycopg://"):
                dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
            self._conn = psycopg.connect(dsn, autocommit=True)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_events (
                    id SERIAL PRIMARY KEY,
                    fid VARCHAR(64) NOT NULL,
                    username VARCHAR(255) NOT NULL,
                    timestamp DOUBLE PRECISION NOT NULL,
                    kind VARCHAR(32) NOT NULL,
                    message TEXT NOT NULL,
                    created_at DOUBLE PRECISION DEFAULT EXTRACT(EPOCH FROM NOW())
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS ix_execution_events_fid
                ON execution_events (fid)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS ix_execution_events_username
                ON execution_events (username)
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS ix_execution_events_fid_kind
                ON execution_events (fid, kind)
            """)
        return self._conn

    def setup(self, username: str, fid: str) -> Tuple[str, str]:
        self._get_connection()
        return (username, fid)

    def save(self, conn: Tuple[str, str], fid: str,
             payload: List[Dict]) -> None:
        if not payload:
            return
        username = conn[0]
        try:
            db_conn = self._get_connection()
            with db_conn.cursor() as cursor:
                for val in payload:
                    cursor.execute(
                        "INSERT INTO execution_events "
                        "(fid, username, timestamp, kind, message) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (fid, username, val.get("timestamp"),
                         val.get("kind"), val.get("payload")))
                    logger.debug(
                        f"saved {val.get('kind')}: {val} for {fid} (pg)")
        except Exception:
            logger.critical(f"failed to save {fid} (pg)", exc_info=True)

    def close(self, conn: Tuple[str, str]) -> None:
        pass


class PostgresExecutionReader:
    """Reads execution events from centralized PostgreSQL.

    Used by the service layer (inputs/outputs/timeline controllers)
    as an alternative to reading per-user SQLite files.
    """

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._engine = None
        self._session_maker = None

    async def _ensure_engine(self):
        if self._engine is None:
            try:
                from sqlalchemy.ext.asyncio import (
                    create_async_engine, async_sessionmaker)
            except ImportError:
                raise ImportError(
                    "asyncpg is required for PostgreSQL support. "
                    "Install with: pip install kodosumi[postgres]")
            self._engine = create_async_engine(
                self.database_url, future=True, echo=False,
                pool_size=10, max_overflow=5)
            self._session_maker = async_sessionmaker(
                self._engine, expire_on_commit=False)

    async def read_events(self, fid: str, after_id: int = 0,
                          kinds: Tuple = ()) -> List[Dict]:
        """Read events for a given execution ID."""
        await self._ensure_engine()
        from sqlalchemy import select, text
        from kodosumi.dtypes import ExecutionEvent

        async with self._session_maker() as session:
            stmt = select(ExecutionEvent).where(
                ExecutionEvent.fid == fid,
                ExecutionEvent.id > after_id)
            if kinds:
                stmt = stmt.where(ExecutionEvent.kind.in_(kinds))
            stmt = stmt.order_by(ExecutionEvent.id)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {"id": r.id, "timestamp": r.timestamp,
                 "kind": r.kind, "message": r.message}
                for r in rows
            ]

    async def close(self):
        if self._engine:
            await self._engine.dispose()


def create_spooler_writer(settings) -> SpoolerWriter:
    """Factory: create the appropriate writer based on config."""
    if settings.EXECUTION_DATABASE:
        return PostgresSpoolerWriter(settings.EXECUTION_DATABASE)
    return SqliteSpoolerWriter(Path(settings.EXEC_DIR))
