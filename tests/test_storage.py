"""Tests for the storage abstraction layer (PR 1: PostgreSQL support).

These tests validate the SqliteSpoolerWriter and storage factory
without requiring the full kodosumi dependency chain (Ray, etc.).
Run with: pytest tests/test_storage.py -v
"""
import sqlite3
import sys
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# SqliteSpoolerWriter tests (no external deps needed beyond stdlib)
# ---------------------------------------------------------------------------

class TestSqliteSpoolerWriter:
    """Tests for the default SQLite writer (existing behavior)."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from kodosumi.storage import SqliteSpoolerWriter
        self.WriterClass = SqliteSpoolerWriter
        self.tmp_path = tmp_path

    def test_setup_creates_directory_and_table(self):
        writer = self.WriterClass(self.tmp_path)
        conn = writer.setup("testuser", "flow123")

        assert isinstance(conn, sqlite3.Connection)
        assert (self.tmp_path / "testuser" / "flow123" / "sqlite3.db").exists()

        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='monitor'")
        assert cursor.fetchone() is not None
        writer.close(conn)

    def test_save_inserts_events(self):
        writer = self.WriterClass(self.tmp_path)
        conn = writer.setup("testuser", "flow456")

        payload = [
            {"timestamp": time.time(), "kind": "status",
             "payload": "running"},
            {"timestamp": time.time(), "kind": "result",
             "payload": '{"data": 42}'},
        ]
        writer.save(conn, "flow456", payload)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM monitor")
        count = cursor.fetchone()[0]
        assert count == 2

        cursor.execute("SELECT kind, message FROM monitor ORDER BY id")
        rows = cursor.fetchall()
        assert rows[0] == ("status", "running")
        assert rows[1] == ("result", '{"data": 42}')
        writer.close(conn)

    def test_save_empty_payload_is_noop(self):
        writer = self.WriterClass(self.tmp_path)
        conn = writer.setup("testuser", "flow789")
        writer.save(conn, "flow789", [])

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM monitor")
        assert cursor.fetchone()[0] == 0
        writer.close(conn)

    def test_multiple_executions_separate_dbs(self):
        writer = self.WriterClass(self.tmp_path)
        conn1 = writer.setup("user1", "flowA")
        conn2 = writer.setup("user2", "flowB")

        writer.save(conn1, "flowA",
                     [{"timestamp": 1.0, "kind": "status",
                       "payload": "a"}])
        writer.save(conn2, "flowB",
                     [{"timestamp": 2.0, "kind": "status",
                       "payload": "b"}])

        cursor1 = conn1.cursor()
        cursor1.execute("SELECT COUNT(*) FROM monitor")
        assert cursor1.fetchone()[0] == 1

        cursor2 = conn2.cursor()
        cursor2.execute("SELECT COUNT(*) FROM monitor")
        assert cursor2.fetchone()[0] == 1

        writer.close(conn1)
        writer.close(conn2)

    def test_save_large_batch(self):
        writer = self.WriterClass(self.tmp_path)
        conn = writer.setup("testuser", "flow_large")

        payload = [
            {"timestamp": time.time() + i, "kind": "action",
             "payload": f"event_{i}"}
            for i in range(100)
        ]
        writer.save(conn, "flow_large", payload)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM monitor")
        assert cursor.fetchone()[0] == 100
        writer.close(conn)

    def test_idempotent_setup(self):
        """setup() can be called multiple times for same fid."""
        writer = self.WriterClass(self.tmp_path)
        conn1 = writer.setup("testuser", "flow_idem")
        writer.save(conn1, "flow_idem",
                     [{"timestamp": 1.0, "kind": "status",
                       "payload": "first"}])
        writer.close(conn1)

        conn2 = writer.setup("testuser", "flow_idem")
        cursor = conn2.cursor()
        cursor.execute("SELECT COUNT(*) FROM monitor")
        assert cursor.fetchone()[0] == 1
        writer.close(conn2)


# ---------------------------------------------------------------------------
# PostgresSpoolerWriter unit tests (no real DB connection needed)
# ---------------------------------------------------------------------------

class TestPostgresSpoolerWriter:
    """Tests for PostgresSpoolerWriter without a real database."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from kodosumi.storage import PostgresSpoolerWriter
        self.WriterClass = PostgresSpoolerWriter

    def test_init_stores_url(self):
        writer = self.WriterClass(
            "postgresql+asyncpg://user:pass@localhost:5432/testdb")
        assert "localhost:5432/testdb" in writer.database_url
        assert writer._conn is None

    def test_setup_returns_username_fid_tuple(self):
        writer = self.WriterClass(
            "postgresql+asyncpg://user:pass@localhost:5432/testdb")
        # Mock the connection to avoid actual DB call
        writer._conn = type("FakeConn", (), {
            "closed": False,
            "execute": lambda self, *a, **kw: None,
        })()
        result = writer.setup("testuser", "flow123")
        assert result == ("testuser", "flow123")

    def test_save_empty_payload_is_noop(self):
        writer = self.WriterClass(
            "postgresql+asyncpg://user:pass@localhost:5432/testdb")
        writer.save(("user", "fid"), "fid", [])

    def test_close_is_noop(self):
        writer = self.WriterClass(
            "postgresql+asyncpg://user:pass@localhost:5432/testdb")
        writer.close(("user", "fid"))


# ---------------------------------------------------------------------------
# Factory function tests
# ---------------------------------------------------------------------------

class TestCreateSpoolerWriter:
    """Tests for the factory function."""

    def test_default_returns_sqlite_writer(self, tmp_path):
        from kodosumi.storage import create_spooler_writer, SqliteSpoolerWriter

        class MockSettings:
            EXEC_DIR = str(tmp_path)
            EXECUTION_DATABASE = None

        writer = create_spooler_writer(MockSettings())
        assert isinstance(writer, SqliteSpoolerWriter)

    def test_postgres_url_returns_postgres_writer(self):
        from kodosumi.storage import create_spooler_writer, PostgresSpoolerWriter

        class MockSettings:
            EXEC_DIR = "/tmp/test"
            EXECUTION_DATABASE = "postgresql+asyncpg://u:p@localhost/db"

        writer = create_spooler_writer(MockSettings())
        assert isinstance(writer, PostgresSpoolerWriter)


# ---------------------------------------------------------------------------
# ExecutionEvent model tests
# ---------------------------------------------------------------------------

class TestExecutionEventModel:
    """Tests for the ExecutionEvent SQLAlchemy model."""

    def test_model_has_correct_tablename(self):
        from kodosumi.dtypes import ExecutionEvent
        assert ExecutionEvent.__tablename__ == "execution_events"

    def test_model_has_required_columns(self):
        from kodosumi.dtypes import ExecutionEvent
        columns = {c.name for c in ExecutionEvent.__table__.columns}
        assert columns == {
            "id", "fid", "username", "timestamp",
            "kind", "message", "created_at"
        }

    def test_model_has_indexes(self):
        from kodosumi.dtypes import ExecutionEvent
        index_names = {
            idx.name for idx in ExecutionEvent.__table__.indexes}
        assert "ix_execution_events_fid" in index_names
        assert "ix_execution_events_username" in index_names
        assert "ix_execution_events_fid_kind" in index_names
