"""
Database layer for expose.db - manages agentic service exposures.

This module provides async SQLite database access for the expose system,
separate from the main admin.db database.
"""

import time
from pathlib import Path
from typing import Optional, List

import aiosqlite

# Database path constant
EXPOSE_DATABASE = "./data/expose.db"


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
    db_path: Optional[str] = None
) -> dict:
    """Create or update an expose item."""
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    now = time.time()
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        # Check if exists
        cursor = await conn.execute(
            "SELECT created FROM expose WHERE name = ?", (name,)
        )
        existing = await cursor.fetchone()

        if existing:
            # Update
            created = existing["created"]
            await conn.execute("""
                UPDATE expose SET
                    display = ?,
                    network = ?,
                    enabled = ?,
                    state = ?,
                    heartbeat = ?,
                    bootstrap = ?,
                    meta = ?,
                    updated = ?
                WHERE name = ?
            """, (display, network, int(enabled), state, heartbeat,
                  bootstrap, meta, now, name))
        else:
            # Insert
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
        return dict(row)


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
    db_path: Optional[str] = None
) -> bool:
    """Update only the meta field of an expose item.

    Does NOT update the 'updated' timestamp to avoid changing the ETag.
    The ETag is used for optimistic concurrency control on form saves.
    """
    if db_path is None:
        db_path = EXPOSE_DATABASE
    _ensure_db_dir(db_path)
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("""
            UPDATE expose SET meta = ?
            WHERE name = ?
        """, (meta, name))
        await conn.commit()
        return cursor.rowcount > 0
