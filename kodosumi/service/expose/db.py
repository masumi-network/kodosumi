"""
Database layer for expose.db - manages agentic service exposures.

This module provides async SQLite database access for the expose system,
separate from the main admin.db database.
"""

import math
import time
from pathlib import Path
from typing import List, Optional

import aiosqlite

# Database path constant
EXPOSE_DATABASE = "./data/expose.db"
_EXPECTED_META_UNSET = object()


def next_expose_etag(current: float) -> float:
    """Return an ETag newer than both the clock and the current row."""
    return max(time.time(), math.nextafter(float(current), math.inf))


def _ensure_db_dir(db_path: str) -> None:
    """Ensure the database directory exists."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


async def init_database(db_path: Optional[str] = None) -> None:
    """Initialize the expose database schema."""
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS expose (
                name TEXT PRIMARY KEY,
                display TEXT,
                network TEXT,
                enabled INTEGER DEFAULT 1,
                state TEXT DEFAULT 'DRAFT',
                heartbeat REAL,
                bootstrap TEXT,
                meta TEXT,
                created REAL NOT NULL,
                updated REAL NOT NULL
            )
        """)
        await conn.commit()


async def get_expose(name: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Get a single expose item by name."""
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM expose WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return None


async def get_all_exposes(db_path: Optional[str] = None) -> List[dict]:
    """Get all expose items."""
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        cursor = await conn.execute(
            "SELECT * FROM expose ORDER BY name"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]


async def upsert_expose(
    name: str,
    display: Optional[str],
    network: Optional[str],
    enabled: bool,
    state: str,
    heartbeat: float,
    bootstrap: Optional[str],
    meta: Optional[str],
    db_path: Optional[str] = None,
    *,
    expected_updated: Optional[float] = None,
    original_name: Optional[str] = None,
) -> Optional[dict]:
    """Create or update an expose item."""
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        is_rename = bool(original_name and original_name != name)
        lookup_name = original_name if is_rename else name
        cursor = await conn.execute(
            "SELECT created, display, network, meta, updated "
            "FROM expose WHERE name = ?",
            (lookup_name,),
        )
        existing = await cursor.fetchone()

        if existing:
            # Preserve display, network, and meta when incoming
            # values are None/empty, so partial updates (e.g. bootstrap-only)
            # don't wipe Masumi registration data or network config.
            # A proper PATCH endpoint is tracked in #41.
            created = existing["created"]
            eff_display = display if display is not None else existing["display"]
            eff_network = network if network is not None else existing["network"]
            eff_meta = meta if meta and meta.strip() else existing["meta"]
            now = next_expose_etag(existing["updated"])

            params = (
                eff_display, eff_network, int(enabled), state,
                heartbeat, bootstrap, eff_meta, now,
            )
            assignments = "" if not is_rename else "name = ?,"
            if is_rename:
                params = (name,) + params
            where = "WHERE name = ?"
            params += (lookup_name,)
            if expected_updated is not None:
                where += " AND updated = ?"
                params += (expected_updated,)
            try:
                updated_row = await conn.execute(f"""
                UPDATE expose SET {assignments}
                    display = ?,
                    network = ?,
                    enabled = ?,
                    state = ?,
                    heartbeat = ?,
                    bootstrap = ?,
                    meta = ?,
                    updated = ?
                {where}
            """, params)
            except aiosqlite.IntegrityError:
                await conn.rollback()
                return None
            if updated_row.rowcount == 0:
                await conn.rollback()
                return None
        else:
            if is_rename or expected_updated is not None:
                return None
            # Insert
            now = time.time()
            created = now
            await conn.execute("""
                INSERT INTO expose (name, display, network, enabled, state,
                                    heartbeat, bootstrap, meta, created, updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, display, network, int(enabled), state, heartbeat,
                  bootstrap, meta, created, now))

        await conn.commit()

        # Return the record
        cursor = await conn.execute(
            "SELECT * FROM expose WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_expose(name: str, db_path: Optional[str] = None) -> bool:
    """Delete an expose item by name. Returns True if deleted."""
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute(
            "DELETE FROM expose WHERE name = ?", (name,)
        )
        await conn.commit()
        return cursor.rowcount > 0


async def update_expose_state(
    name: str,
    state: str,
    heartbeat: float,
    db_path: Optional[str] = None
) -> bool:
    """Update only the state and heartbeat of an expose item.

    Does not touch ``updated`` — that field is reserved for user-initiated
    edits via upsert_expose() and serves as the ETag for optimistic
    concurrency control on the edit form.
    """
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("""
            UPDATE expose SET state = ?, heartbeat = ?
            WHERE name = ?
        """, (state, heartbeat, name))
        await conn.commit()
        return cursor.rowcount > 0


async def update_expose_meta(
    name: str,
    meta: str,
    db_path: Optional[str] = None,
    *,
    updated: Optional[float] = None,
    expected_updated: Optional[float] = None,
    expected_meta: object = _EXPECTED_META_UNSET,
) -> bool:
    """Update only the meta field of an expose item.

    Registry writes can supply ``updated`` to invalidate stale edit forms.
    Boot and health writes omit it, so they do not change the form ETag.
    """
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as conn:
        if updated is None:
            statement = "UPDATE expose SET meta = ?"
            params = (meta,)
        else:
            statement = "UPDATE expose SET meta = ?, updated = ?"
            params = (meta, updated)

        where = ["name = ?"]
        params += (name,)
        if expected_updated is not None:
            where.append("updated = ?")
            params += (expected_updated,)
        if expected_meta is not _EXPECTED_META_UNSET:
            if expected_meta is None:
                where.append("meta IS NULL")
            else:
                where.append("meta = ?")
                params += (expected_meta,)
        cursor = await conn.execute(
            f"{statement} WHERE {' AND '.join(where)}", params)
        await conn.commit()
        return cursor.rowcount > 0
