"""
Tests for Sumi Protocol Job Management Endpoints (Phase 5).

Tests MIP-003 job management:
- POST /sumi/{expose-name}/{meta-name}/start_job
- GET /sumi/status/{job_id}
"""

import pytest
import tempfile
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from kodosumi.service.sumi.control import (
    _get_job_status_from_db,
)
from kodosumi.service.sumi.models import (
    StartJobRequest,
    StartJobResponse,
    JobStatusResponse,
)
from kodosumi.service.sumi.hash import create_input_hash
from kodosumi.helper import now


# =============================================================================
# StartJobRequest Model Tests
# =============================================================================


class TestStartJobRequestModel:
    """Test StartJobRequest model."""

    def test_minimal(self):
        req = StartJobRequest(identifier_from_purchaser="order-123")
        assert req.identifier_from_purchaser == "order-123"
        assert req.input_data is None

    def test_with_input_data(self):
        req = StartJobRequest(
            identifier_from_purchaser="order-456",
            input_data={"query": "test", "count": 10},
        )
        assert req.input_data["query"] == "test"
        assert req.input_data["count"] == 10

    def test_serialization(self):
        req = StartJobRequest(
            identifier_from_purchaser="order-789",
            input_data={"field": "value"},
        )
        data = req.model_dump()
        assert data["identifier_from_purchaser"] == "order-789"
        assert data["input_data"]["field"] == "value"


# =============================================================================
# StartJobResponse Model Tests
# =============================================================================


class TestStartJobResponseModel:
    """Test StartJobResponse model."""

    def test_success(self):
        resp = StartJobResponse(
            job_id="abc123",
            status="success",
            identifierFromPurchaser="order-123",
            input_hash="sha256hash",
            status_url="/sumi/status/abc123",
        )
        assert resp.status == "success"
        assert resp.job_id == "abc123"
        assert resp.input_hash == "sha256hash"

    def test_error(self):
        resp = StartJobResponse(
            job_id="",
            status="error",
            identifierFromPurchaser="order-123",
            input_hash="sha256hash",
            status_url="",
        )
        assert resp.status == "error"
        assert resp.job_id == ""


# =============================================================================
# JobStatusResponse Model Tests
# =============================================================================


class TestJobStatusResponseModel:
    """Test JobStatusResponse model."""

    def test_running(self):
        resp = JobStatusResponse(job_id="abc123", status="running")
        assert resp.status == "running"
        assert resp.result is None
        assert resp.error is None

    def test_completed(self):
        resp = JobStatusResponse(
            job_id="abc123",
            status="completed",
            result={"output": "done"},
            startedAt=1234567890.0,
            updatedAt=1234567891.0,
            runtime=1.0,
        )
        assert resp.status == "completed"
        assert resp.result["output"] == "done"
        assert resp.startedAt == 1234567890.0
        assert resp.updatedAt == 1234567891.0
        assert resp.runtime == 1.0

    def test_failed(self):
        resp = JobStatusResponse(
            job_id="abc123",
            status="failed",
            error="Something went wrong",
        )
        assert resp.status == "failed"
        assert resp.error == "Something went wrong"

    def test_awaiting_input(self):
        resp = JobStatusResponse(
            job_id="abc123",
            status="awaiting_input",
        )
        assert resp.status == "awaiting_input"


# =============================================================================
# Input Hash Integration Tests
# =============================================================================


class TestInputHashIntegration:
    """Test input hash calculation in job context."""

    def test_hash_calculation(self):
        identifier = "order-12345"
        input_data = {"query": "test value"}

        hash_value = create_input_hash(input_data, identifier)

        assert len(hash_value) == 64
        assert hash_value == create_input_hash(input_data, identifier)

    def test_hash_with_none_input(self):
        identifier = "order-empty"

        hash_value = create_input_hash(None, identifier)

        assert len(hash_value) == 64


# =============================================================================
# Database Status Query Tests
# =============================================================================


class TestGetJobStatusFromDb:
    """Test _get_job_status_from_db helper."""

    def _create_test_db(self, tmpdir):
        """Create a test database with monitor table."""
        db_path = os.path.join(tmpdir, "sqlite3.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE monitor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                kind TEXT,
                message TEXT
            )
        """)
        return conn, db_path

    @pytest.fixture
    def temp_db(self):
        """Create a temporary database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            conn, db_path = self._create_test_db(tmpdir)
            yield conn, db_path
            conn.close()

    @pytest.mark.asyncio
    async def test_running_status(self, temp_db):
        """Test status query for running job."""
        conn, db_path = temp_db
        ts = now()

        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts, "status", "running"),
        )
        conn.commit()

        result, pending_locks = await _get_job_status_from_db(conn, "test-job")

        assert result.job_id == "test-job"
        assert result.status == "running"
        assert result.result is None

    @pytest.mark.asyncio
    async def test_completed_status(self, temp_db):
        """Test status query for completed job."""
        conn, db_path = temp_db
        ts = now()

        # STATUS_END = "finished" in kodosumi.const
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts, "status", "finished"),
        )
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts + 0.1, "final", '{"type": "dict", "dict": {"output": "done"}}'),
        )
        conn.commit()

        result, _ = await _get_job_status_from_db(conn, "test-job")

        assert result.status == "completed"
        assert result.result is not None

    @pytest.mark.asyncio
    async def test_error_status(self, temp_db):
        """Test status query for failed job."""
        conn, db_path = temp_db
        ts = now()

        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts, "status", "error"),
        )
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts + 0.1, "error", "Something went wrong"),
        )
        conn.commit()

        result, _ = await _get_job_status_from_db(conn, "test-job")

        assert result.status == "failed"
        assert result.error == "Something went wrong"

    @pytest.mark.asyncio
    async def test_awaiting_input_status(self, temp_db):
        """Test status query for job awaiting input (lock)."""
        conn, db_path = temp_db
        ts = now()

        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts, "status", "running"),
        )
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts + 0.1, "lock", '{"type": "dict", "dict": {"lid": "lock-1"}}'),
        )
        conn.commit()

        result, pending_locks = await _get_job_status_from_db(conn, "test-job")

        assert result.status == "awaiting_input"
        assert "lock-1" in pending_locks

    @pytest.mark.asyncio
    async def test_lock_released(self, temp_db):
        """Test that released locks don't affect status."""
        conn, db_path = temp_db
        ts = now()

        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts, "status", "running"),
        )
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts + 0.1, "lock", '{"type": "dict", "dict": {"lid": "lock-1"}}'),
        )
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts + 0.2, "lease", '{"type": "dict", "dict": {"lid": "lock-1"}}'),
        )
        conn.commit()

        result, pending_locks = await _get_job_status_from_db(conn, "test-job")

        # Lock was released, should be running
        assert result.status == "running"
        assert "lock-1" not in pending_locks

    @pytest.mark.asyncio
    async def test_identifier_from_meta(self, temp_db):
        """Test extraction of identifier_from_purchaser from meta."""
        conn, db_path = temp_db
        ts = now()

        meta_json = '{"type": "dict", "dict": {"fid": "test-job", "extra": {"identifier_from_purchaser": "order-123"}}}'

        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts, "status", "running"),
        )
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts + 0.1, "meta", meta_json),
        )
        conn.commit()

        result, _ = await _get_job_status_from_db(conn, "test-job")

        assert result.identifierFromPurchaser == "order-123"

    @pytest.mark.asyncio
    async def test_runtime_calculation(self, temp_db):
        """Test runtime calculation from timestamps."""
        conn, db_path = temp_db
        ts1 = 1000.0
        ts2 = 1005.5

        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts1, "status", "starting"),
        )
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (ts2, "status", "end"),
        )
        conn.commit()

        result, _ = await _get_job_status_from_db(conn, "test-job")

        assert result.startedAt == ts1
        assert result.updatedAt == ts2
        assert result.runtime == 5.5


# =============================================================================
# Status Mapping Tests
# =============================================================================


class TestStatusMapping:
    """Test Kodosumi to MIP-003 status mapping."""

    @pytest.mark.asyncio
    async def test_finished_status_maps_to_completed(self):
        """Test 'finished' status (STATUS_END) maps to 'completed'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE monitor (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    kind TEXT,
                    message TEXT
                )
            """)
            # STATUS_END = "finished" in kodosumi.const
            conn.execute(
                "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
                (now(), "status", "finished"),
            )
            conn.commit()

            result, _ = await _get_job_status_from_db(conn, "job")
            conn.close()

            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_error_maps_to_failed(self):
        """Test 'error' status maps to 'failed'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            conn = sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE monitor (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    kind TEXT,
                    message TEXT
                )
            """)
            conn.execute(
                "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
                (now(), "status", "error"),
            )
            conn.commit()

            result, _ = await _get_job_status_from_db(conn, "job")
            conn.close()

            assert result.status == "failed"
