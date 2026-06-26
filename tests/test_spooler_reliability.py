"""
tests/test_spooler_reliability.py
----------------------------------
Pure unit tests for the three reliability features added in v1.2.0:

  #74a — drain-before-kill (_drain_remaining helper)
  #74b — spooler_attached() guard (serve.py + sumi/control.py entry points)
  #69  — _sd_notify() helper (sd_notify watchdog / readiness)

No Ray cluster required — all Ray interactions are mocked.

Infrastructure note (NOT deployed here, for reference only):
  # environments/{loki,odin}/systemd/spooler.service companion changes:
  #   Type=notify
  #   NotifyAccess=main
  #   WatchdogSec=120
"""

import os
import socket
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers imported directly — no Ray needed
# ---------------------------------------------------------------------------
from kodosumi.spooler import _drain_remaining, _sd_notify, spooler_attached


# ===========================================================================
# #74a — _drain_remaining
# ===========================================================================

class _FakeQueue:
    """Minimal queue stub matching the Ray ActorQueue surface used by
    _drain_remaining (synchronous — avoids any Ray dependency)."""

    def __init__(self, items):
        self._items = list(items)

    def size(self):
        return len(self._items)

    def get_nowait_batch(self, n):
        batch = self._items[:n]
        self._items = self._items[n:]
        return batch


def test_drain_remaining_pulls_all_events():
    """All N events must be saved when N > batch_size."""
    n_events = 47
    batch_size = 10
    items = [{"timestamp": float(i), "kind": "action", "payload": f"ev{i}"}
             for i in range(n_events)]
    queue = _FakeQueue(items)

    saved = []
    _drain_remaining(queue, saved.extend, batch_size)

    assert queue.size() == 0, "queue must be empty after drain"
    assert len(saved) == n_events, (
        f"expected {n_events} events saved, got {len(saved)}")


def test_drain_remaining_empty_queue_is_noop():
    """Empty queue → no save calls, returns 0."""
    queue = _FakeQueue([])
    saved = []
    result = _drain_remaining(queue, saved.extend, 10)
    assert result == 0
    assert saved == []


def test_drain_remaining_returns_count():
    """Return value equals number of events drained."""
    items = [{"timestamp": 1.0, "kind": "status", "payload": "x"}] * 5
    queue = _FakeQueue(items)
    result = _drain_remaining(queue, lambda b: None, 3)
    assert result == 5


def test_drain_remaining_breaks_on_actor_died_error():
    """ActorDiedError from get_nowait_batch must be caught; no exception
    propagates and whatever was already drained is counted."""

    class _DyingQueue:
        def __init__(self):
            self._calls = 0

        def size(self):
            return 10  # always claims items remain

        def get_nowait_batch(self, n):
            self._calls += 1
            if self._calls == 1:
                return [{"timestamp": 1.0, "kind": "action", "payload": "ok"}]
            # Simulate ActorDiedError on second call
            raise RuntimeError("ActorDiedError: simulated")

    queue = _DyingQueue()
    saved = []
    # Must not raise, must save the first batch
    result = _drain_remaining(queue, saved.extend, 5)
    assert len(saved) == 1
    assert result == 1


def test_drain_remaining_no_progress_guard():
    """If the queue size never shrinks (size() always returns non-zero but
    get_nowait_batch returns empty), the loop must stop via the no-progress
    guard rather than running max_iterations times."""

    class _StuckQueue:
        def size(self):
            return 5  # never decreases

        def get_nowait_batch(self, n):
            return []  # never returns items

    queue = _StuckQueue()
    saved = []
    # With a low max_iterations to keep the test fast
    result = _drain_remaining(queue, saved.extend, 5, max_iterations=100)
    assert result == 0
    assert saved == []


def test_drain_remaining_exact_batch_boundary():
    """Events that fit exactly into one batch are all drained."""
    items = [{"timestamp": float(i), "kind": "result", "payload": f"r{i}"}
             for i in range(10)]
    queue = _FakeQueue(items)
    saved = []
    _drain_remaining(queue, saved.extend, 10)
    assert len(saved) == 10
    assert queue.size() == 0


def test_drain_saves_to_sqlite_via_spooler(tmp_path):
    """Integration: _drain_remaining integrates with Spooler.save() writing
    into a real SQLite DB (no Ray needed)."""
    from kodosumi.spooler import Spooler

    spooler = Spooler(exec_dir=tmp_path)
    username = "testuser"
    fid = "drain-test-fid"
    conn = spooler.setup_database(username, fid)

    items = [{"timestamp": float(i), "kind": "action", "payload": f"msg{i}"}
             for i in range(25)]
    queue = _FakeQueue(items)

    _drain_remaining(
        queue,
        lambda batch: spooler.save(conn, fid, batch),
        batch_size=8,
    )
    conn.close()

    db_path = tmp_path / username / fid / "sqlite3.db"
    with sqlite3.connect(str(db_path)) as c:
        rows = c.execute("SELECT COUNT(*) FROM monitor").fetchone()[0]
    assert rows == 25


# ===========================================================================
# #74b — spooler_attached()
# ===========================================================================

def test_spooler_attached_returns_true_when_actor_exists():
    """ray.get_actor succeeding → True."""
    with patch("kodosumi.spooler.ray.get_actor", return_value=MagicMock()):
        assert spooler_attached() is True


def test_spooler_attached_returns_false_when_actor_missing():
    """ray.get_actor raising ValueError (actor not found) → False."""
    with patch("kodosumi.spooler.ray.get_actor",
               side_effect=ValueError("no actor")):
        assert spooler_attached() is False


def test_spooler_attached_returns_false_on_any_exception():
    """ray.get_actor raising any other exception → False (defensive)."""
    with patch("kodosumi.spooler.ray.get_actor",
               side_effect=RuntimeError("ray down")):
        assert spooler_attached() is False


# ===========================================================================
# #69 — _sd_notify()
# ===========================================================================

def test_sd_notify_noop_when_no_socket(monkeypatch):
    """No NOTIFY_SOCKET env var → no-op, no exception."""
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    # Must not raise
    _sd_notify("READY=1")


def test_sd_notify_noop_on_nonexistent_path(monkeypatch, tmp_path):
    """NOTIFY_SOCKET pointing to a non-existent path → no-op, no exception."""
    monkeypatch.setenv("NOTIFY_SOCKET", str(tmp_path / "does_not_exist.sock"))
    _sd_notify("READY=1")


def test_sd_notify_sends_correct_datagram(monkeypatch):
    """Bind a real AF_UNIX SOCK_DGRAM socket and assert the datagram arrives.

    Uses /tmp directly with a short name to stay within AF_UNIX's 104-char
    path limit on macOS.
    """
    sock_path = "/tmp/kodo_test_sdnotify_r1.sock"
    # Remove stale socket if it exists
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(sock_path)
    server.settimeout(2.0)

    monkeypatch.setenv("NOTIFY_SOCKET", sock_path)
    try:
        _sd_notify("READY=1")
        data, _ = server.recvfrom(64)
        assert data == b"READY=1"
    finally:
        server.close()
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass


def test_sd_notify_watchdog_datagram(monkeypatch):
    """WATCHDOG=1 is sent correctly."""
    sock_path = "/tmp/kodo_test_sdnotify_w1.sock"
    try:
        os.unlink(sock_path)
    except FileNotFoundError:
        pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(sock_path)
    server.settimeout(2.0)

    monkeypatch.setenv("NOTIFY_SOCKET", sock_path)
    try:
        _sd_notify("WATCHDOG=1")
        data, _ = server.recvfrom(64)
        assert data == b"WATCHDOG=1"
    finally:
        server.close()
        try:
            os.unlink(sock_path)
        except FileNotFoundError:
            pass
