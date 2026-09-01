"""
Controller for the audit log endpoints.

Serves the audit log that records the operator actions on an expose.

All endpoints require operator role authentication.
"""

import logging
from pathlib import Path

import litestar
from litestar import get
from litestar.datastructures import State

from kodosumi.service.jwt import operator_guard

logger = logging.getLogger(__name__)


class AuditLogControl(litestar.Controller):
    """Controller for audit log viewing."""

    path = "/audit"
    tags = ["Audit"]
    guards = [operator_guard]

    @get(
        "/stream",
        summary="Stream audit log",
        description="Stream audit log entries from offset. Only INFO level (no sensitive details).",
        operation_id="audit_stream",
    )
    async def stream_audit_log(
        self,
        state: State,
        offset: int = 0,
        limit: int = 100,
    ) -> dict:
        """
        Stream audit log entries from a given byte offset.

        Args:
            offset: Byte offset to start reading from (default: 0)
            limit: Maximum number of lines to return (default: 100)

        Returns:
            dict with:
            - lines: List of log lines (INFO level only)
            - next_offset: Byte offset for next read
            - file_size: Current file size
        """
        audit_log_path = Path(state["settings"].AUDIT_LOG_FILE).resolve()

        if not audit_log_path.exists():
            return {
                "lines": [f"Audit log file not found: {audit_log_path}"],
                "next_offset": 0,
                "file_size": 0,
            }

        file_size = audit_log_path.stat().st_size

        # If offset is beyond file size (e.g., after rotation), reset to 0
        if offset > file_size:
            offset = 0

        lines = []
        next_offset = offset
        try:
            with open(audit_log_path, "r", encoding="utf-8") as f:
                f.seek(offset)
                bytes_read = 0
                max_bytes = 64 * 1024  # 64KB max read per request

                while True:
                    line = f.readline()
                    if not line:
                        break

                    line_bytes = len(line.encode("utf-8"))
                    bytes_read += line_bytes

                    # Filter: only INFO level and above (no DEBUG)
                    # Format: "2024-01-01 00:00:00,000 INFO - message"
                    if " INFO " in line or " WARNING " in line or " ERROR " in line:
                        lines.append(line.rstrip())

                    if len(lines) >= limit or bytes_read >= max_bytes:
                        break

                next_offset = f.tell()

        except Exception as e:
            return {
                "lines": [f"Error reading audit log: {e}"],
                "next_offset": offset,
                "file_size": file_size,
            }

        return {
            "lines": lines,
            "next_offset": next_offset,
            "file_size": file_size,
        }

    @get(
        "/info",
        summary="Audit log info",
        description="Get audit log file information.",
        operation_id="audit_info",
    )
    async def audit_log_info(self, state: State) -> dict:
        """Get audit log file information."""
        audit_log_path = Path(state["settings"].AUDIT_LOG_FILE)

        if not audit_log_path.exists():
            return {
                "exists": False,
                "path": str(audit_log_path),
                "size": 0,
                "max_bytes": state["settings"].AUDIT_LOG_MAX_BYTES,
                "backup_count": state["settings"].AUDIT_LOG_BACKUP_COUNT,
            }

        return {
            "exists": True,
            "path": str(audit_log_path),
            "size": audit_log_path.stat().st_size,
            "max_bytes": state["settings"].AUDIT_LOG_MAX_BYTES,
            "backup_count": state["settings"].AUDIT_LOG_BACKUP_COUNT,
            "modified": audit_log_path.stat().st_mtime,
        }
