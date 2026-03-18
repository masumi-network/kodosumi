"""Execution index for fast dashboard queries.

Maintains an in-memory index of execution metadata, built once on startup
by scanning all SQLite databases, then kept current via incremental refresh.

PERFORMANCE:
- Startup: full scan of all DBs (~30-60s for 12K executions)
- Refresh (every 60s): only rescans non-final executions + discovers new ones
  (~10 DB opens instead of 12K)
- Dashboard queries served from memory in milliseconds
"""
import asyncio
import json
import logging
import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime

from kodosumi.const import STATUS_FINAL, DB_FILE

logger = logging.getLogger(__name__)


class ExecutionIndex:
    """In-memory index of execution metadata with smart incremental refresh.

    On startup, scans all execution directories and reads metadata from each
    SQLite database. After that, only non-final executions (running, starting,
    awaiting, payment) are rescanned on each refresh cycle. Final executions
    (finished, error) are immutable and never touched again.

    New executions are detected by comparing known exec_ids against the
    current directory listing.
    """

    def __init__(self, exec_dir: Path, refresh_interval: int = 60):
        self.exec_dir = exec_dir
        self.refresh_interval = refresh_interval
        # Main index: exec_id -> (user_id, exec_dir, metadata)
        self._index: Dict[str, Tuple[str, Path, Dict[str, Any]]] = {}
        # Set of exec_ids with final status — never rescan these
        self._final: Set[str] = set()
        self.last_refresh: float = 0
        self._refresh_lock = asyncio.Lock()
        self._is_refreshing = False

    def _read_metadata(
        self, db_path: Path, user_id: str, exec_id: str
    ) -> Optional[Dict[str, Any]]:
        """Extract metadata from a single execution SQLite DB (synchronous).

        Uses a single combined query to minimize DB time.
        """
        if not db_path.exists():
            return None
        try:
            with sqlite3.connect(
                f"file:{db_path}?mode=ro", uri=True, timeout=5.0
            ) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                # Single query for status, error, meta
                cur.execute(
                    "SELECT kind, message, timestamp FROM monitor "
                    "WHERE kind IN ('status', 'error', 'meta') "
                    "ORDER BY kind, timestamp DESC, id DESC"
                )
                rows = cur.fetchall()

                status_value = "unknown"
                error = None
                error_timestamp = None
                meta = {}
                found = set()

                for row in rows:
                    kind = row["kind"]
                    if kind in found:
                        continue
                    found.add(kind)
                    if kind == "status":
                        status_value = row["message"]
                    elif kind == "error":
                        error = row["message"]
                        error_timestamp = row["timestamp"]
                    elif kind == "meta":
                        try:
                            meta = json.loads(row["message"])
                        except (json.JSONDecodeError, KeyError):
                            pass

                # Timing
                cur.execute(
                    "SELECT MIN(timestamp) as start, "
                    "MAX(timestamp) as end FROM monitor"
                )
                timing = cur.fetchone()
                start_time = timing["start"] if timing else None
                end_time = timing["end"] if timing else None

                # Agent name
                agent_name = "Unknown"
                if isinstance(meta, dict):
                    d = meta.get("dict", meta)
                    agent_name = (
                        d.get("summary") or d.get("entry_point", "Unknown")
                    )

                return {
                    "fid": exec_id,
                    "user_id": user_id,
                    "agent_name": agent_name,
                    "status": status_value,
                    "meta": meta,
                    "error": error,
                    "error_timestamp": error_timestamp,
                    "start_time": start_time,
                    "end_time": end_time,
                    "runtime": (
                        end_time - start_time
                        if start_time and end_time
                        else None
                    ),
                }
        except Exception as e:
            logger.debug(f"Cannot read metadata from {db_path}: {e}")
            return None

    def _find_db(self, exec_dir: Path) -> Optional[Path]:
        """Find the SQLite database file in an execution directory."""
        db_path = exec_dir / DB_FILE
        if db_path.exists():
            return db_path
        db_path = exec_dir / "db.sqlite"
        if db_path.exists():
            return db_path
        return None

    def _is_final(self, metadata: Dict[str, Any]) -> bool:
        """Check if execution has reached a terminal state.

        Final states: finished, error, unknown (no status row = orphaned DB).
        Everything else (starting, running, awaiting, payment) is active.
        """
        status = metadata.get("status", "unknown")
        return status in STATUS_FINAL or status == "unknown"

    def full_scan(self) -> None:
        """Scan all execution directories and build the complete index.

        This is synchronous and should be run in a thread pool.
        Called once on startup.
        """
        if not self.exec_dir.exists():
            logger.warning(f"Execution directory not found: {self.exec_dir}")
            self.last_refresh = datetime.now().timestamp()
            return

        index: Dict[str, Tuple[str, Path, Dict[str, Any]]] = {}
        final: Set[str] = set()
        skipped = 0

        for user_dir in self.exec_dir.iterdir():
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue
            user_id = user_dir.name
            try:
                for exec_dir in user_dir.iterdir():
                    if not exec_dir.is_dir() or exec_dir.name.startswith("."):
                        continue
                    exec_id = exec_dir.name
                    db_path = self._find_db(exec_dir)
                    if not db_path:
                        skipped += 1
                        continue
                    metadata = self._read_metadata(db_path, user_id, exec_id)
                    if metadata is None:
                        skipped += 1
                        continue
                    index[exec_id] = (user_id, exec_dir, metadata)
                    if self._is_final(metadata):
                        final.add(exec_id)
            except (OSError, PermissionError) as e:
                logger.warning(f"Cannot access {user_dir}: {e}")

        self._index = index
        self._final = final
        self.last_refresh = datetime.now().timestamp()

        logger.info(
            f"Execution index built: {len(index)} executions "
            f"({len(final)} final, {len(index) - len(final)} active, "
            f"{skipped} skipped)"
        )

    def incremental_refresh(self) -> None:
        """Refresh only non-final executions and discover new ones.

        This is synchronous and should be run in a thread pool.
        Called every refresh_interval seconds after startup.
        """
        if not self.exec_dir.exists():
            return

        # 1. Discover all current exec_ids via directory listing
        current_ids: Dict[str, Tuple[str, Path]] = {}
        for user_dir in self.exec_dir.iterdir():
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue
            user_id = user_dir.name
            try:
                for exec_dir in user_dir.iterdir():
                    if not exec_dir.is_dir() or exec_dir.name.startswith("."):
                        continue
                    current_ids[exec_dir.name] = (user_id, exec_dir)
            except (OSError, PermissionError):
                continue

        # 2. Find new executions (in filesystem but not in index)
        new_ids = set(current_ids.keys()) - set(self._index.keys())

        # 3. Find non-final executions that need rescan
        rescan_ids = set(self._index.keys()) - self._final

        to_scan = new_ids | rescan_ids
        updated = 0
        newly_final = 0

        for exec_id in to_scan:
            if exec_id in current_ids:
                user_id, exec_dir = current_ids[exec_id]
            elif exec_id in self._index:
                user_id, exec_dir, _ = self._index[exec_id]
            else:
                continue

            db_path = self._find_db(exec_dir)
            if not db_path:
                continue

            metadata = self._read_metadata(db_path, user_id, exec_id)
            if metadata is None:
                continue

            self._index[exec_id] = (user_id, exec_dir, metadata)
            updated += 1

            if self._is_final(metadata):
                self._final.add(exec_id)
                newly_final += 1

        # 4. Remove executions whose directories were deleted
        removed_ids = set(self._index.keys()) - set(current_ids.keys())
        for exec_id in removed_ids:
            del self._index[exec_id]
            self._final.discard(exec_id)

        self.last_refresh = datetime.now().timestamp()

        if to_scan or removed_ids:
            logger.info(
                f"Index refresh: scanned {len(to_scan)} "
                f"({len(new_ids)} new, {len(rescan_ids)} active), "
                f"{newly_final} became final, "
                f"{len(removed_ids)} removed. "
                f"Total: {len(self._index)} "
                f"({len(self._final)} final)"
            )

    # -- Public API for dashboard endpoints --

    def get_executions_with_metadata(
        self,
    ) -> List[Tuple[str, str, Path, Dict[str, Any]]]:
        """Return all indexed executions sorted newest-first.

        Returns list of (user_id, exec_id, exec_dir, metadata).
        """
        items = [
            (user_id, exec_id, exec_dir, metadata)
            for exec_id, (user_id, exec_dir, metadata) in self._index.items()
        ]
        items.sort(key=lambda x: x[1], reverse=True)
        return items

    @property
    def count(self) -> int:
        return len(self._index)

    @property
    def user_count(self) -> int:
        return len({v[0] for v in self._index.values()})

    @property
    def active_count(self) -> int:
        return len(self._index) - len(self._final)

    @property
    def is_ready(self) -> bool:
        return self.last_refresh > 0

    async def refresh(self) -> None:
        """Run incremental refresh in background thread."""
        async with self._refresh_lock:
            if self._is_refreshing:
                return
            self._is_refreshing = True
            try:
                await asyncio.to_thread(self.incremental_refresh)
            finally:
                self._is_refreshing = False


async def start_refresh_loop(index: ExecutionIndex) -> None:
    """Background coroutine: refresh index every interval."""
    logger.info(
        f"Index refresh loop started (interval: {index.refresh_interval}s)"
    )
    while True:
        try:
            await asyncio.sleep(index.refresh_interval)
            await index.refresh()
        except asyncio.CancelledError:
            logger.info("Index refresh loop cancelled")
            break
        except Exception as e:
            logger.error(f"Index refresh error: {e}", exc_info=True)
