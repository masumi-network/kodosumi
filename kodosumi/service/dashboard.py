"""Dashboard API endpoints for analytics and monitoring."""
import json
import logging
import aiosqlite
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta

from litestar import Controller, get, Request
from litestar.datastructures import State
from litestar.exceptions import NotFoundException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kodosumi.dtypes import Role
from kodosumi.service.auth import get_user_details

logger = logging.getLogger(__name__)


class DashboardAPI(Controller):
    """API endpoints for dashboard analytics."""

    tags = ["Dashboard"]

    async def _get_user_map(self, transaction: AsyncSession) -> Dict[str, str]:
        """Get mapping of user IDs to user names."""
        query = select(Role.id, Role.name)
        result = await transaction.execute(query)
        return {str(row[0]): row[1] for row in result.fetchall()}

    def _get_executions(
        self, state: State
    ) -> Tuple[Optional[str], List[Tuple[str, str, Path, Dict[str, Any]]]]:
        """Get executions from index. Returns (status_message, executions).

        If index is not ready yet, status_message explains why.
        """
        index = state.get("execution_index")
        if index is None:
            return ("Execution index not available.", [])
        if not index.is_ready:
            return ("Execution index is being built. Please wait...", [])
        return (None, index.get_executions_with_metadata())

    async def _query_execution_db(
        self, db_path: Path, query: str, params: tuple = ()
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

    @get("/running-agents")
    async def get_running_agents(
        self,
        request: Request,
        state: State,
        transaction: AsyncSession,
        hours: int = 24,
        agent_name: Optional[str] = None,
        user: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get running or recently active agents with filtering and pagination."""
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)
        user_map = await self._get_user_map(transaction)

        msg, all_executions = self._get_executions(state)
        if msg:
            return {
                "status": "index_building",
                "message": msg,
                "total": 0,
                "agents": [],
                "offset": 0,
                "limit": limit,
                "has_more": False,
                "filters": {
                    "hours": hours,
                    "agent_name": agent_name,
                    "user": user,
                    "status": status,
                    "search": search,
                },
            }

        cutoff_time = datetime.now() - timedelta(hours=hours)
        agents = []
        total_matching = 0
        skipped = 0

        for user_id, exec_id, exec_dir, metadata in all_executions:
            # Role-based filter
            if not is_operator and user_id != current_user_id:
                continue
            if user and user != user_id:
                continue
            if not metadata or not metadata.get("start_time"):
                continue

            # Time filter
            if datetime.fromtimestamp(metadata["start_time"]) < cutoff_time:
                continue

            # Attribute filters
            if agent_name and agent_name.lower() not in metadata["agent_name"].lower():
                continue
            if status and status.lower() != metadata["status"].lower():
                continue
            if search:
                searchable = f"{metadata['fid']} {metadata['agent_name']} {user_id} {metadata['status']}".lower()
                if search.lower() not in searchable:
                    continue

            total_matching += 1

            # Pagination
            if skipped < offset:
                skipped += 1
                continue
            if len(agents) < limit:
                metadata["user_name"] = user_map.get(user_id, user_id)
                metadata["_exec_dir"] = exec_dir
                agents.append(metadata)

        # Count files only for the page being displayed
        for agent in agents:
            exec_dir = agent.pop("_exec_dir", None)
            if exec_dir:
                in_dir = exec_dir / "in"
                agent["upload_count"] = (
                    len([f for f in in_dir.iterdir() if f.is_file()])
                    if in_dir.is_dir()
                    else 0
                )
                out_dir = exec_dir / "out"
                agent["download_count"] = (
                    len([f for f in out_dir.iterdir() if f.is_file()])
                    if out_dir.is_dir()
                    else 0
                )
            else:
                agent["upload_count"] = 0
                agent["download_count"] = 0

        return {
            "total": total_matching,
            "agents": agents,
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(agents) < total_matching,
            "filters": {
                "hours": hours,
                "agent_name": agent_name,
                "user": user,
                "status": status,
                "search": search,
            },
        }

    @get("/errors")
    async def get_errors(
        self,
        request: Request,
        state: State,
        transaction: AsyncSession,
        hours: int = 24,
        agent_name: Optional[str] = None,
        user: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get recent errors from all executions."""
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)

        msg, all_executions = self._get_executions(state)
        if msg:
            return {"status": "index_building", "message": msg, "total": 0, "errors": []}

        cutoff_time = datetime.now() - timedelta(hours=hours)
        errors = []

        for user_id, exec_id, exec_dir, metadata in all_executions:
            if not is_operator and user_id != current_user_id:
                continue
            if user and user != user_id:
                continue
            if not metadata.get("error") or not metadata.get("error_timestamp"):
                continue
            if datetime.fromtimestamp(metadata["error_timestamp"]) < cutoff_time:
                continue
            if agent_name and agent_name.lower() not in metadata.get("agent_name", "").lower():
                continue
            if status and status.lower() != metadata.get("status", "").lower():
                continue
            if search:
                searchable = f"{exec_id} {metadata.get('agent_name', '')} {user_id}".lower()
                if search.lower() not in searchable:
                    continue

            try:
                error_data = json.loads(metadata["error"]) if isinstance(metadata["error"], str) else metadata["error"]
            except json.JSONDecodeError:
                error_data = {"error": metadata["error"]}

            errors.append({
                "fid": exec_id,
                "user_id": user_id,
                "timestamp": metadata["error_timestamp"],
                "error": error_data,
            })

        errors.sort(key=lambda x: x["timestamp"], reverse=True)
        return {"total": len(errors), "errors": errors[:limit]}

    @get("/timeline")
    async def get_timeline(
        self,
        request: Request,
        state: State,
        transaction: AsyncSession,
        hours: int = 24,
        agent_name: Optional[str] = None,
        user: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get execution timeline data for charting."""
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)

        msg, all_executions = self._get_executions(state)
        if msg:
            return {"status": "index_building", "message": msg, "total": 0, "executions": []}

        cutoff_time = datetime.now() - timedelta(hours=hours)
        executions = []

        for user_id, exec_id, exec_dir, metadata in all_executions:
            if not is_operator and user_id != current_user_id:
                continue
            if user and user != user_id:
                continue
            if not metadata or not metadata.get("start_time"):
                continue
            if datetime.fromtimestamp(metadata["start_time"]) < cutoff_time:
                continue
            if agent_name and agent_name.lower() not in metadata.get("agent_name", "").lower():
                continue
            if status and status.lower() != metadata.get("status", "").lower():
                continue
            if search:
                searchable = f"{metadata.get('fid', '')} {metadata.get('agent_name', '')} {user_id} {metadata.get('status', '')}".lower()
                if search.lower() not in searchable:
                    continue
            executions.append(metadata)

        return {"total": len(executions), "executions": executions}

    @get("/agent-stats")
    async def get_agent_stats(
        self,
        request: Request,
        state: State,
        transaction: AsyncSession,
        hours: int = 999999,
        agent_name: Optional[str] = None,
        user: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get per-agent statistics."""
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)
        user_map = await self._get_user_map(transaction)

        msg, all_executions = self._get_executions(state)
        if msg:
            return {
                "status": "index_building", "message": msg,
                "total_executions": 0, "by_status": {},
                "by_user": {}, "avg_runtime": 0, "error_rate": 0,
            }

        cutoff_time = datetime.now() - timedelta(hours=hours)
        stats: Dict[str, Any] = {
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

        for user_id, exec_id, exec_dir, metadata in all_executions:
            if not is_operator and user_id != current_user_id:
                continue
            if user and user != user_id:
                continue
            if not metadata or not metadata.get("start_time"):
                continue
            if datetime.fromtimestamp(metadata["start_time"]) < cutoff_time:
                continue
            if agent_name and agent_name.lower() not in metadata.get("agent_name", "").lower():
                continue
            if status and status.lower() != metadata.get("status", "").lower():
                continue
            if search:
                searchable = f"{metadata.get('fid', '')} {metadata.get('agent_name', '')} {user_id} {metadata.get('status', '')}".lower()
                if search.lower() not in searchable:
                    continue

            filtered_count += 1
            exec_status = metadata["status"]
            stats["by_status"][exec_status] = stats["by_status"].get(exec_status, 0) + 1
            stats["by_user"][user_id] = stats["by_user"].get(user_id, 0) + 1
            if metadata.get("runtime"):
                total_runtime += metadata["runtime"]
                runtime_count += 1
            if metadata.get("error"):
                error_count += 1

        stats["total_executions"] = filtered_count
        if runtime_count > 0:
            stats["avg_runtime"] = total_runtime / runtime_count
        if filtered_count > 0:
            stats["error_rate"] = error_count / filtered_count
        stats["user_map"] = user_map
        return stats

    @get("/execution/{fid:str}/details")
    async def get_execution_details(
        self,
        fid: str,
        request: Request,
        state: State,
        transaction: AsyncSession,
    ) -> Dict[str, Any]:
        """Get detailed information about a specific execution."""
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)
        exec_path = Path(state["settings"].EXEC_DIR)

        # Find execution database
        db_path = None
        exec_user_id = None
        for user_dir in exec_path.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith("."):
                potential_path = user_dir / fid / "sqlite3.db"
                if not potential_path.exists():
                    potential_path = user_dir / fid / "db.sqlite"
                if potential_path.exists():
                    db_path = potential_path
                    exec_user_id = user_dir.name
                    break

        if not db_path:
            raise NotFoundException(detail=f"Execution {fid} not found")
        if not is_operator and exec_user_id != current_user_id:
            raise NotFoundException(detail=f"Execution {fid} not found")

        events = await self._query_execution_db(
            db_path,
            "SELECT id, timestamp, kind, message FROM monitor ORDER BY timestamp ASC",
        )

        # Build metadata from events
        metadata = self._extract_metadata_from_events(events, exec_user_id, fid, db_path.parent)

        return {
            "metadata": metadata,
            "events": events,
            "total_events": len(events),
        }

    def _extract_metadata_from_events(
        self, events: List[Dict], user_id: str, exec_id: str, exec_dir: Path
    ) -> Dict[str, Any]:
        """Extract metadata from already-fetched event rows."""
        status_value = "unknown"
        error = None
        error_timestamp = None
        meta = {}
        start_time = None
        end_time = None

        for event in events:
            ts = event.get("timestamp")
            if start_time is None or (ts and ts < start_time):
                start_time = ts
            if end_time is None or (ts and ts > end_time):
                end_time = ts

            kind = event["kind"]
            if kind == "status":
                status_value = event["message"]
            elif kind == "error":
                error = event["message"]
                error_timestamp = ts
            elif kind == "meta" and not meta:
                try:
                    meta = json.loads(event["message"])
                except (json.JSONDecodeError, KeyError):
                    pass

        agent_name = "Unknown"
        if isinstance(meta, dict):
            d = meta.get("dict", meta)
            agent_name = d.get("summary") or d.get("entry_point", "Unknown")

        # Count files
        in_dir = exec_dir / "in"
        upload_count = len([f for f in in_dir.iterdir() if f.is_file()]) if in_dir.is_dir() else 0
        out_dir = exec_dir / "out"
        download_count = len([f for f in out_dir.iterdir() if f.is_file()]) if out_dir.is_dir() else 0

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
            "runtime": end_time - start_time if start_time and end_time else None,
            "upload_count": upload_count,
            "download_count": download_count,
        }

    @get("/execution/{fid:str}/files")
    async def get_execution_files(
        self,
        fid: str,
        request: Request,
        state: State,
        transaction: AsyncSession,
    ) -> Dict[str, Any]:
        """Get list of files for a specific execution."""
        current_user = await get_user_details(request.user, transaction)
        is_operator = current_user.operator
        current_user_id = str(current_user.id)
        exec_path = Path(state["settings"].EXEC_DIR)

        exec_dir = None
        user_id = None
        for user_dir in exec_path.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith("."):
                potential_dir = user_dir / fid
                if potential_dir.exists():
                    exec_dir = potential_dir
                    user_id = user_dir.name
                    break

        if not exec_dir:
            raise NotFoundException(detail=f"Execution {fid} not found")
        if not is_operator and user_id != current_user_id:
            raise NotFoundException(detail=f"Execution {fid} not found")

        uploads = []
        in_dir = exec_dir / "in"
        if in_dir.exists() and in_dir.is_dir():
            for f in in_dir.iterdir():
                if f.is_file():
                    uploads.append({
                        "name": f.name,
                        "size": f.stat().st_size,
                        "path": f"in/{f.name}",
                        "url": f"/files/{user_id}/{fid}/in/{f.name}",
                    })

        downloads = []
        out_dir = exec_dir / "out"
        if out_dir.exists() and out_dir.is_dir():
            for f in out_dir.iterdir():
                if f.is_file():
                    downloads.append({
                        "name": f.name,
                        "size": f.stat().st_size,
                        "path": f"out/{f.name}",
                        "url": f"/files/{user_id}/{fid}/out/{f.name}",
                    })

        return {
            "fid": fid,
            "uploads": uploads,
            "downloads": downloads,
            "upload_count": len(uploads),
            "download_count": len(downloads),
        }
