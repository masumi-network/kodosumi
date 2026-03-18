"""Tests for ExecutionIndex — full_scan, incremental_refresh, and edge cases."""
import json
import sqlite3
import time
from pathlib import Path

import pytest

from kodosumi.service.execution_index import ExecutionIndex


def _create_execution_db(exec_dir: Path, events: list[dict]):
    """Helper: create a sqlite3.db with monitor table and events."""
    exec_dir.mkdir(parents=True, exist_ok=True)
    db_path = exec_dir / "sqlite3.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE monitor ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  timestamp REAL NOT NULL,"
        "  kind TEXT NOT NULL,"
        "  message TEXT NOT NULL"
        ")"
    )
    for event in events:
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (event["timestamp"], event["kind"], event["message"]),
        )
    conn.commit()
    conn.close()
    return db_path


def _make_meta(summary="TestAgent", entry_point="module:func"):
    return json.dumps({"summary": summary, "entry_point": entry_point})


def _make_events(status="finished", agent_name="TestAgent", error=None,
                 start=None, end=None):
    """Build a standard set of monitor events."""
    t0 = start or time.time() - 60
    t1 = end or time.time()
    events = [
        {"timestamp": t0, "kind": "meta", "message": _make_meta(agent_name)},
        {"timestamp": t0, "kind": "status", "message": "starting"},
        {"timestamp": t0 + 1, "kind": "status", "message": "running"},
        {"timestamp": t1, "kind": "status", "message": status},
    ]
    if error:
        events.append(
            {"timestamp": t1, "kind": "error", "message": error}
        )
    return events


class TestFullScan:

    def test_empty_directory(self, tmp_path):
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 0
        assert index.is_ready

    def test_missing_directory(self, tmp_path):
        index = ExecutionIndex(tmp_path / "nonexistent")
        index.full_scan()
        assert index.count == 0
        assert index.is_ready

    def test_single_finished_execution(self, tmp_path):
        user_dir = tmp_path / "user-1"
        _create_execution_db(
            user_dir / "exec-001",
            _make_events(status="finished", agent_name="MyAgent"),
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()

        assert index.count == 1
        assert index.active_count == 0  # finished = final

        execs = index.get_executions_with_metadata()
        assert len(execs) == 1
        user_id, exec_id, exec_dir, meta = execs[0]
        assert user_id == "user-1"
        assert exec_id == "exec-001"
        assert meta["status"] == "finished"
        assert meta["agent_name"] == "MyAgent"
        assert meta["runtime"] is not None

    def test_multiple_users_and_executions(self, tmp_path):
        for u in range(3):
            for e in range(5):
                _create_execution_db(
                    tmp_path / f"user-{u}" / f"exec-{u:02d}{e:02d}",
                    _make_events(status="finished"),
                )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 15
        assert index.user_count == 3
        assert index.active_count == 0

    def test_running_execution_is_active(self, tmp_path):
        _create_execution_db(
            tmp_path / "user-1" / "exec-run",
            _make_events(status="running"),
        )
        _create_execution_db(
            tmp_path / "user-1" / "exec-done",
            _make_events(status="finished"),
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 2
        assert index.active_count == 1

    def test_error_execution_is_final(self, tmp_path):
        _create_execution_db(
            tmp_path / "user-1" / "exec-err",
            _make_events(status="error", error="something broke"),
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 1
        assert index.active_count == 0
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["error"] == "something broke"

    def test_unknown_status_is_final(self, tmp_path):
        """Execution with no status row (empty DB) treated as final."""
        exec_dir = tmp_path / "user-1" / "exec-orphan"
        exec_dir.mkdir(parents=True)
        db_path = exec_dir / "sqlite3.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE monitor ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  timestamp REAL NOT NULL,"
            "  kind TEXT NOT NULL,"
            "  message TEXT NOT NULL"
            ")"
        )
        # Insert only a meta row, no status
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (time.time(), "meta", _make_meta()),
        )
        conn.commit()
        conn.close()

        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 1
        assert index.active_count == 0  # unknown = final

    def test_sorted_newest_first(self, tmp_path):
        t = time.time()
        _create_execution_db(
            tmp_path / "u" / "aaa",
            _make_events(start=t - 100, end=t - 90),
        )
        _create_execution_db(
            tmp_path / "u" / "zzz",
            _make_events(start=t - 50, end=t - 40),
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        execs = index.get_executions_with_metadata()
        # zzz > aaa alphabetically, so zzz first
        assert execs[0][1] == "zzz"
        assert execs[1][1] == "aaa"

    def test_skips_hidden_dirs(self, tmp_path):
        _create_execution_db(
            tmp_path / ".hidden_user" / "exec-1",
            _make_events(),
        )
        _create_execution_db(
            tmp_path / "user-1" / ".hidden_exec",
            _make_events(),
        )
        _create_execution_db(
            tmp_path / "user-1" / "exec-visible",
            _make_events(),
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 1

    def test_no_db_file_skipped(self, tmp_path):
        (tmp_path / "user-1" / "exec-empty").mkdir(parents=True)
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 0


class TestIncrementalRefresh:

    def test_new_execution_discovered(self, tmp_path):
        _create_execution_db(
            tmp_path / "u" / "exec-1",
            _make_events(status="finished"),
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 1

        # Add a new execution
        _create_execution_db(
            tmp_path / "u" / "exec-2",
            _make_events(status="finished"),
        )
        index.incremental_refresh()
        assert index.count == 2

    def test_running_becomes_finished(self, tmp_path):
        exec_dir = tmp_path / "u" / "exec-1"
        _create_execution_db(exec_dir, _make_events(status="running"))

        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.active_count == 1

        # Update the DB: add a "finished" status
        conn = sqlite3.connect(str(exec_dir / "sqlite3.db"))
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (time.time(), "status", "finished"),
        )
        conn.commit()
        conn.close()

        index.incremental_refresh()
        assert index.active_count == 0
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["status"] == "finished"

    def test_final_not_rescanned(self, tmp_path):
        """Final executions should not be read again on refresh."""
        exec_dir = tmp_path / "u" / "exec-done"
        _create_execution_db(exec_dir, _make_events(status="finished"))

        index = ExecutionIndex(tmp_path)
        index.full_scan()

        # Delete the DB file — if refresh tried to read it, it would fail
        (exec_dir / "sqlite3.db").unlink()
        # But the directory still exists, so it's still in current_ids
        # The final set prevents rescan, so metadata stays
        index.incremental_refresh()
        assert index.count == 1  # still there from cache

    def test_deleted_execution_removed(self, tmp_path):
        exec_dir = tmp_path / "u" / "exec-1"
        _create_execution_db(exec_dir, _make_events(status="finished"))

        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.count == 1

        # Delete the entire execution directory
        import shutil
        shutil.rmtree(exec_dir)

        index.incremental_refresh()
        assert index.count == 0

    def test_awaiting_is_rescanned(self, tmp_path):
        exec_dir = tmp_path / "u" / "exec-wait"
        _create_execution_db(exec_dir, _make_events(status="awaiting"))

        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.active_count == 1

        # Transition to payment
        conn = sqlite3.connect(str(exec_dir / "sqlite3.db"))
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (time.time(), "status", "payment"),
        )
        conn.commit()
        conn.close()

        index.incremental_refresh()
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["status"] == "payment"
        assert index.active_count == 1  # still active

    def test_payment_to_finished(self, tmp_path):
        exec_dir = tmp_path / "u" / "exec-pay"
        _create_execution_db(exec_dir, _make_events(status="payment"))

        index = ExecutionIndex(tmp_path)
        index.full_scan()
        assert index.active_count == 1

        conn = sqlite3.connect(str(exec_dir / "sqlite3.db"))
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (time.time(), "status", "finished"),
        )
        conn.commit()
        conn.close()

        index.incremental_refresh()
        assert index.active_count == 0
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["status"] == "finished"


class TestMetadataExtraction:

    def test_agent_name_from_summary(self, tmp_path):
        _create_execution_db(
            tmp_path / "u" / "e1",
            [
                {"timestamp": 1.0, "kind": "meta",
                 "message": json.dumps({"summary": "Cool Agent"})},
                {"timestamp": 2.0, "kind": "status", "message": "finished"},
            ],
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["agent_name"] == "Cool Agent"

    def test_agent_name_from_entry_point(self, tmp_path):
        _create_execution_db(
            tmp_path / "u" / "e1",
            [
                {"timestamp": 1.0, "kind": "meta",
                 "message": json.dumps({"entry_point": "mymod:run"})},
                {"timestamp": 2.0, "kind": "status", "message": "finished"},
            ],
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["agent_name"] == "mymod:run"

    def test_agent_name_from_nested_dict(self, tmp_path):
        _create_execution_db(
            tmp_path / "u" / "e1",
            [
                {"timestamp": 1.0, "kind": "meta",
                 "message": json.dumps({"dict": {"summary": "Nested Agent"}})},
                {"timestamp": 2.0, "kind": "status", "message": "finished"},
            ],
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["agent_name"] == "Nested Agent"

    def test_runtime_calculation(self, tmp_path):
        _create_execution_db(
            tmp_path / "u" / "e1",
            [
                {"timestamp": 100.0, "kind": "meta", "message": _make_meta()},
                {"timestamp": 100.0, "kind": "status", "message": "running"},
                {"timestamp": 110.0, "kind": "status", "message": "finished"},
            ],
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["start_time"] == 100.0
        assert meta["end_time"] == 110.0
        assert meta["runtime"] == 10.0

    def test_corrupt_meta_json(self, tmp_path):
        _create_execution_db(
            tmp_path / "u" / "e1",
            [
                {"timestamp": 1.0, "kind": "meta", "message": "not json{{{"},
                {"timestamp": 2.0, "kind": "status", "message": "finished"},
            ],
        )
        index = ExecutionIndex(tmp_path)
        index.full_scan()
        meta = index.get_executions_with_metadata()[0][3]
        assert meta["agent_name"] == "Unknown"

    def test_file_counting_not_in_index(self, tmp_path):
        """Index metadata should not include file counts (done on-demand)."""
        exec_dir = tmp_path / "u" / "e1"
        _create_execution_db(exec_dir, _make_events())
        (exec_dir / "in").mkdir()
        (exec_dir / "in" / "file.txt").write_text("data")
        (exec_dir / "out").mkdir()
        (exec_dir / "out" / "result.csv").write_text("data")

        index = ExecutionIndex(tmp_path)
        index.full_scan()
        meta = index.get_executions_with_metadata()[0][3]
        # Index doesn't count files — that's done by dashboard on-demand
        assert "upload_count" not in meta
        assert "download_count" not in meta
