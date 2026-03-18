"""Dashboard API endpoints for analytics and monitoring."""
import aiosqlite
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

from litestar import Controller, get, Request
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kodosumi.dtypes import Role
from kodosumi.service.auth import get_user_details


class DashboardAPI(Controller):
    """API endpoints for dashboard analytics."""

    tags = ["Dashboard"]

    @get("/debug/user", summary="Debug current user", description="Returns current user's ID, name, email, operator status, and active flag. For debugging authentication.")
    async def debug_user(
        self,
        request: Request,
        transaction: AsyncSession
    ) -> Dict[str, Any]:
        """Debug endpoint to check current user and operator status."""
        current_user = await get_user_details(request.user, transaction)
        return {
            "user_id": str(current_user.id),
            "name": current_user.name,
            "email": current_user.email,
            "operator": current_user.operator,
            "active": current_user.active
        }

    async def _get_user_map(self, transaction: AsyncSession) -> Dict[str, str]:
        """Get mapping of user IDs to user names."""
        query = select(Role.id, Role.name)
        result = await transaction.execute(query)
        return {str(row[0]): row[1] for row in result.fetchall()}

    async def _get_all_execution_dbs(self, state: State) -> List[Path]:
        """Find all execution database files."""
        exec_path = Path(state["settings"].EXEC_DIR)
        if not exec_path.exists():
            return []

        db_files = []
        for user_dir in exec_path.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                for exec_dir in user_dir.iterdir():
                    if exec_dir.is_dir() and not exec_dir.name.startswith('.'):
                        # Try both db.sqlite and sqlite3.db
                        db_file = exec_dir / "db.sqlite"
                        if not db_file.exists():
                            db_file = exec_dir / "sqlite3.db"
                        if db_file.exists():
                            db_files.append(db_file)
        return db_files

    async def _query_execution_db(
        self,
        db_path: Path,
        query: str,
        params: tuple = ()
    ) -> List[Dict[str, Any]]:
        """Query a single execution database."""
        try:
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(query, params) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception:
            return []

    async def _get_execution_metadata(self, db_path: Path) -> Optional[Dict[str, Any]]:
        """Extract metadata from execution database."""
        # Parse execution ID and user from path
        parts = db_path.parts
        exec_id = parts[-2]
        user_id = parts[-3]
        exec_dir = db_path.parent

        # Get status (plain string, not JSON)
        status_rows = await self._query_execution_db(
            db_path,
            "SELECT message FROM monitor WHERE kind = 'status' ORDER BY timestamp DESC, id DESC LIMIT 1"
        )
        status_value = status_rows[0]["message"] if status_rows else "unknown"

        # Get error if exists (plain text or JSON)
        error_rows = await self._query_execution_db(
            db_path,
            "SELECT message, timestamp FROM monitor WHERE kind = 'error' ORDER BY timestamp DESC, id DESC LIMIT 1"
        )
        error = error_rows[0]["message"] if error_rows else None
        error_timestamp = error_rows[0]["timestamp"] if error_rows else None

        # Get meta info (JSON)
        meta_rows = await self._query_execution_db(
            db_path,
            "SELECT message FROM monitor WHERE kind = 'meta' ORDER BY timestamp ASC LIMIT 1"
        )
        meta = {}
        if meta_rows:
            try:
                meta = json.loads(meta_rows[0]["message"])
            except (json.JSONDecodeError, KeyError):
                pass

        # Get timing info
        timing_rows = await self._query_execution_db(
            db_path,
            "SELECT MIN(timestamp) as start, MAX(timestamp) as end FROM monitor"
        )
        timing = timing_rows[0] if timing_rows else {"start": None, "end": None}

        # Extract agent name from meta
        agent_name = "Unknown"
        if isinstance(meta, dict):
            if "dict" in meta:
                meta_dict = meta["dict"]
                agent_name = meta_dict.get("summary") or meta_dict.get("entry_point", "Unknown")
            else:
                agent_name = meta.get("summary") or meta.get("entry_point", "Unknown")

        # Count files in in/ and out/ directories
        upload_count = 0
        download_count = 0

        in_dir = exec_dir / "in"
        if in_dir.exists() and in_dir.is_dir():
            upload_count = len([f for f in in_dir.iterdir() if f.is_file()])

        out_dir = exec_dir / "out"
        if out_dir.exists() and out_dir.is_dir():
            download_count = len([f for f in out_dir.iterdir() if f.is_file()])

        return {
            "fid": exec_id,
            "user_id": user_id,
            "agent_name": agent_name,
            "status": status_value,
            "meta": meta,
            "error": error,
            "error_timestamp": error_timestamp,
            "start_time": timing["start"],
            "end_time": timing["end"],
            "runtime": timing["end"] - timing["start"] if timing["start"] and timing["end"] else None,
            "upload_count": upload_count,
            "download_count": download_count,
        }

    @get("/running-agents", summary="List running agents", description="Get all currently running or recently active agent executions. Supports filtering by hours, agent_name, user, status, and search. Non-operators see only their own executions.")
    async def get_running_agents(
        self,
        request: Request,
        state: State,
        transaction: AsyncSession,
        hours: int = 24,
        agent_name: Optional[str] = None,
        user: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get all currently running or recently active agents with filtering."""
        # Get current user and check if operator
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)

        print(f"[DEBUG] User: {current_user.name}, ID: {current_user_id}, Operator: {is_operator}")

        db_files = await self._get_all_execution_dbs(state)
        user_map = await self._get_user_map(transaction)

        agents = []
        cutoff_time = datetime.now() - timedelta(hours=hours)

        for db_path in db_files:
            metadata = await self._get_execution_metadata(db_path)
            if metadata and metadata["start_time"]:
                start_dt = datetime.fromtimestamp(metadata["start_time"])

                # Time filter
                if start_dt < cutoff_time:
                    continue

                # Role-based filter: non-operators see only their executions
                if not is_operator and metadata["user_id"] != current_user_id:
                    print(f"[DEBUG] Filtering out: exec_user={metadata['user_id']}, current={current_user_id}")
                    continue

                # Apply filters
                if agent_name and agent_name.lower() not in metadata["agent_name"].lower():
                    continue
                if user and user != metadata["user_id"]:
                    continue
                if status and status.lower() != metadata["status"].lower():
                    continue

                # Global search filter
                if search:
                    search_lower = search.lower()
                    searchable = f"{metadata['fid']} {metadata['agent_name']} {metadata['user_id']} {metadata['status']}".lower()
                    if search_lower not in searchable:
                        continue

                # Add user name
                metadata["user_name"] = user_map.get(metadata["user_id"], metadata["user_id"])
                agents.append(metadata)

        # Sort by start time, most recent first
        agents.sort(key=lambda x: x["start_time"] or 0, reverse=True)

        return {
            "total": len(agents),
            "agents": agents,
            "filters": {
                "hours": hours,
                "agent_name": agent_name,
                "user": user,
                "status": status,
                "search": search
            }
        }

    @get("/errors", summary="List recent errors", description="Get recent error events from all executions within the specified time window. Non-operators see only their own errors.")
    async def get_errors(
        self,
        request: Request,
        state: State,
        transaction: AsyncSession,
        hours: int = 24,
        limit: int = 100
    ) -> Dict[str, Any]:
        """Get recent errors from all executions."""
        # Get current user and check if operator
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)

        db_files = await self._get_all_execution_dbs(state)

        errors = []
        cutoff_time = datetime.now() - timedelta(hours=hours)

        for db_path in db_files:
            # Parse execution ID from path
            parts = db_path.parts
            exec_id = parts[-2]
            user_id = parts[-3]

            # Role-based filter: non-operators see only their executions
            if not is_operator and user_id != current_user_id:
                continue

            error_rows = await self._query_execution_db(
                db_path,
                "SELECT message, timestamp FROM monitor WHERE kind = 'error' ORDER BY timestamp DESC"
            )

            for row in error_rows:
                error_time = datetime.fromtimestamp(row["timestamp"])
                if error_time > cutoff_time:

                    try:
                        error_data = json.loads(row["message"])
                    except json.JSONDecodeError:
                        error_data = {"error": row["message"]}

                    errors.append({
                        "fid": exec_id,
                        "user_id": user_id,
                        "timestamp": row["timestamp"],
                        "error": error_data,
                    })

        # Sort by timestamp, most recent first
        errors.sort(key=lambda x: x["timestamp"], reverse=True)

        return {
            "total": len(errors),
            "errors": errors[:limit]
        }

    @get("/timeline", summary="Get execution timeline", description="Get execution timeline data for charting. Returns all executions within the specified time window sorted by start time.")
    async def get_timeline(
        self,
        request: Request,
        state: State,
        transaction: AsyncSession,
        hours: int = 24
    ) -> Dict[str, Any]:
        """Get execution timeline data for charting."""
        # Get current user and check if operator
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)

        db_files = await self._get_all_execution_dbs(state)

        executions = []
        cutoff_time = datetime.now() - timedelta(hours=hours)

        for db_path in db_files:
            metadata = await self._get_execution_metadata(db_path)
            if metadata and metadata["start_time"]:
                # Role-based filter: non-operators see only their executions
                if not is_operator and metadata["user_id"] != current_user_id:
                    continue

                start_dt = datetime.fromtimestamp(metadata["start_time"])
                if start_dt > cutoff_time:
                    executions.append(metadata)

        # Sort by start time
        executions.sort(key=lambda x: x["start_time"] or 0)

        return {
            "total": len(executions),
            "executions": executions
        }

    @get("/agent-stats", summary="Get agent statistics", description="Get aggregate statistics: total executions, status breakdown, per-user counts, average runtime, and error rate.")
    async def get_agent_stats(
        self,
        request: Request,
        state: State,
        transaction: AsyncSession
    ) -> Dict[str, Any]:
        """Get per-agent statistics."""
        # Get current user and check if operator
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)

        db_files = await self._get_all_execution_dbs(state)

        stats = {
            "total_executions": 0,
            "by_status": {},
            "by_user": {},
            "avg_runtime": 0,
            "error_rate": 0,
        }

        total_runtime = 0
        runtime_count = 0
        error_count = 0
        filtered_count = 0

        for db_path in db_files:
            metadata = await self._get_execution_metadata(db_path)
            if metadata:
                # Role-based filter: non-operators see only their executions
                if not is_operator and metadata["user_id"] != current_user_id:
                    continue

                filtered_count += 1

                # Count by status
                status = metadata["status"]
                stats["by_status"][status] = stats["by_status"].get(status, 0) + 1

                # Count by user
                user = metadata["user_id"]
                stats["by_user"][user] = stats["by_user"].get(user, 0) + 1

                # Calculate runtime
                if metadata["runtime"]:
                    total_runtime += metadata["runtime"]
                    runtime_count += 1

                # Count errors
                if metadata["error"]:
                    error_count += 1

        # Update total with filtered count
        stats["total_executions"] = filtered_count

        # Calculate averages
        if runtime_count > 0:
            stats["avg_runtime"] = total_runtime / runtime_count

        if filtered_count > 0:
            stats["error_rate"] = error_count / filtered_count

        return stats

    @get("/execution/{fid:str}/details", summary="Get execution details", description="Get detailed information about a specific execution including all monitor events and metadata.")
    async def get_execution_details(
        self,
        fid: str,
        request: Request,
        state: State,
        transaction: AsyncSession
    ) -> Dict[str, Any]:
        """Get detailed information about a specific execution."""
        # Get current user and check if operator
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)

        exec_path = Path(state["settings"].EXEC_DIR)

        # Find the execution database
        db_path = None
        exec_user_id = None
        for user_dir in exec_path.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                # Try both db.sqlite and sqlite3.db
                potential_path = user_dir / fid / "db.sqlite"
                if not potential_path.exists():
                    potential_path = user_dir / fid / "sqlite3.db"
                if potential_path.exists():
                    db_path = potential_path
                    exec_user_id = user_dir.name
                    break

        if not db_path:
            raise NotFoundException(detail=f"Execution {fid} not found")

        # Role-based access control
        if not is_operator and exec_user_id != current_user_id:
            raise NotFoundException(detail=f"Execution {fid} not found")

        # Get all events
        events = await self._query_execution_db(
            db_path,
            "SELECT id, timestamp, kind, message FROM monitor ORDER BY timestamp ASC"
        )

        # Get metadata
        metadata = await self._get_execution_metadata(db_path)

        return {
            "metadata": metadata,
            "events": events,
            "total_events": len(events)
        }

    @get("/execution/{fid:str}/files", summary="Get execution files", description="List uploaded input files and generated output files for a specific execution.")
    async def get_execution_files(
        self,
        fid: str,
        request: Request,
        state: State,
        transaction: AsyncSession
    ) -> Dict[str, Any]:
        """Get list of files for a specific execution."""
        # Get current user and check if operator
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)

        exec_path = Path(state["settings"].EXEC_DIR)

        # Find the execution directory
        exec_dir = None
        user_id = None
        for user_dir in exec_path.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                potential_dir = user_dir / fid
                if potential_dir.exists():
                    exec_dir = potential_dir
                    user_id = user_dir.name
                    break

        if not exec_dir:
            raise NotFoundException(detail=f"Execution {fid} not found")

        # Role-based access control
        if not is_operator and user_id != current_user_id:
            raise NotFoundException(detail=f"Execution {fid} not found")

        # Get upload files
        uploads = []
        in_dir = exec_dir / "in"
        if in_dir.exists() and in_dir.is_dir():
            for f in in_dir.iterdir():
                if f.is_file():
                    uploads.append({
                        "name": f.name,
                        "size": f.stat().st_size,
                        "path": f"in/{f.name}",
                        "url": f"/files/{user_id}/{fid}/in/{f.name}"
                    })

        # Get download files
        downloads = []
        out_dir = exec_dir / "out"
        if out_dir.exists() and out_dir.is_dir():
            for f in out_dir.iterdir():
                if f.is_file():
                    downloads.append({
                        "name": f.name,
                        "size": f.stat().st_size,
                        "path": f"out/{f.name}",
                        "url": f"/files/{user_id}/{fid}/out/{f.name}"
                    })

        return {
            "fid": fid,
            "uploads": uploads,
            "downloads": downloads,
            "upload_count": len(uploads),
            "download_count": len(downloads)
        }
