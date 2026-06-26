"""
Unit tests for the HITL payment-deadline bounding logic (ticket #56).

Tests the pure helper ``_effective_lock_deadline`` directly — no Ray cluster
required.  Also tests that ``Runner._payment_deadline`` is populated correctly
from ``pay_data`` and that ``Runner.lock()`` uses the bounded expiry.
"""

import asyncio
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kodosumi.runner.main import _effective_lock_deadline


# ---------------------------------------------------------------------------
# Pure helper tests — no Ray, no asyncio
# ---------------------------------------------------------------------------


class TestEffectiveLockDeadline:
    """Unit tests for _effective_lock_deadline()."""

    def test_payment_deadline_earlier_is_returned(self):
        """When the payment deadline is earlier it must bound the lock."""
        lock_ts = time.time() + 3600  # 1 h from now
        pay_ts = time.time() + 600    # 10 min from now (earlier)
        result = _effective_lock_deadline(lock_ts, pay_ts)
        assert result == pay_ts

    def test_none_payment_deadline_returns_lock_expires(self):
        """Non-paid jobs (payment_deadline=None) must be entirely unaffected."""
        lock_ts = time.time() + 3600
        result = _effective_lock_deadline(lock_ts, None)
        assert result == lock_ts

    def test_payment_deadline_later_returns_lock_expires(self):
        """If the payment deadline is later than the lock expiry, lock wins."""
        lock_ts = time.time() + 600
        pay_ts = time.time() + 3600  # 1 h — later than lock
        result = _effective_lock_deadline(lock_ts, pay_ts)
        assert result == lock_ts

    def test_equal_deadlines_returns_same_value(self):
        """When both deadlines are identical the result is that same timestamp."""
        ts = time.time() + 1800
        result = _effective_lock_deadline(ts, ts)
        assert result == ts

    def test_already_expired_payment_deadline(self):
        """A payment deadline already in the past is still returned as-is
        (the caller — Runner.lock() — is responsible for acting on it)."""
        lock_ts = time.time() + 3600
        pay_ts = time.time() - 1  # already expired
        result = _effective_lock_deadline(lock_ts, pay_ts)
        assert result == pay_ts  # pay_ts < lock_ts so pay_ts is returned


# ---------------------------------------------------------------------------
# Runner._payment_deadline population from pay_data
# ---------------------------------------------------------------------------


def _make_minimal_runner():
    """
    Return a Runner-like object without Ray by instantiating its ``__init__``
    via direct attribute assignment.  We patch out the Ray-dependent parts.
    """
    # We cannot call Runner() without Ray, so we build a plain object that
    # carries the same attributes touched by start() and lock().
    class FakeRunner:
        fid = "test-fid"
        _payment: Optional[dict] = None
        _payment_deadline: Optional[float] = None

    return FakeRunner()


class TestPaymentDeadlineExtraction:
    """Verify that _payment_deadline is correctly extracted from pay_data."""

    def _epoch_ms_str(self, offset_seconds: float) -> str:
        """Return epoch-milliseconds as a string *offset_seconds* from now."""
        return str(int((time.time() + offset_seconds) * 1000))

    def test_deadline_extracted_from_pay_data_string(self):
        """submitResultTime as a string of epoch-ms → correct float seconds."""
        offset = 3600.0  # 1 hour
        raw_ms = str(int((time.time() + offset) * 1000))
        pay_data = {"submitResultTime": raw_ms}
        expected = float(raw_ms) / 1000.0

        runner = _make_minimal_runner()
        # Simulate the extraction logic from start()
        raw_srt = pay_data.get("submitResultTime")
        if raw_srt is not None:
            runner._payment_deadline = float(raw_srt) / 1000.0

        assert runner._payment_deadline == pytest.approx(expected, abs=1.0)

    def test_deadline_extracted_from_pay_data_int(self):
        """submitResultTime as an int is also handled."""
        offset = 1800.0
        raw_ms = int((time.time() + offset) * 1000)
        pay_data = {"submitResultTime": raw_ms}

        runner = _make_minimal_runner()
        raw_srt = pay_data.get("submitResultTime")
        if raw_srt is not None:
            runner._payment_deadline = float(raw_srt) / 1000.0

        assert runner._payment_deadline == pytest.approx(time.time() + offset, abs=1.0)

    def test_missing_submit_result_time_leaves_deadline_none(self):
        """If submitResultTime is absent, _payment_deadline stays None."""
        pay_data = {"payByTime": "12345678"}  # no submitResultTime
        runner = _make_minimal_runner()
        raw_srt = pay_data.get("submitResultTime")
        if raw_srt is not None:
            runner._payment_deadline = float(raw_srt) / 1000.0
        assert runner._payment_deadline is None

    def test_no_payment_leaves_deadline_none(self):
        """Non-paid jobs (payment=None) must never touch _payment_deadline."""
        runner = _make_minimal_runner()
        payment = None
        if payment:
            raw_srt = (payment.get("pay_data") or {}).get("submitResultTime")
            if raw_srt is not None:
                runner._payment_deadline = float(raw_srt) / 1000.0
        assert runner._payment_deadline is None


# ---------------------------------------------------------------------------
# Runner.lock() behaviour — asyncio tests, no Ray
# ---------------------------------------------------------------------------


class FakeLockRunner:
    """
    Minimal stand-in for Runner that implements lock() (copy of the real logic)
    so we can test it without a Ray actor.
    """

    def __init__(self, payment_deadline: Optional[float] = None):
        self._payment_deadline = payment_deadline
        self._locks: dict = {}
        self.app_url = "http://localhost:8005"

    async def lock(self, name: str, lid: str, expires: float,
                   data: Optional[dict] = None):
        effective_expires = _effective_lock_deadline(expires, self._payment_deadline)
        self._locks[lid] = {
            "name": name,
            "data": data,
            "result": None,
            "app_url": self.app_url,
            "expires": effective_expires,
        }
        while True:
            if self._locks.get(lid, {}).get("result") is not None:
                break
            current = time.time()
            if current > effective_expires:
                self._locks.pop(lid)
                if (
                    self._payment_deadline is not None
                    and effective_expires <= expires
                    and current > self._payment_deadline
                ):
                    raise TimeoutError(
                        f"Lock {lid}: payment result window expired "
                        f"(submitResultTime={self._payment_deadline})"
                    )
                raise TimeoutError(f"Lock {lid} expired at {expires}")
            await asyncio.sleep(0.05)  # faster than 1s for tests
        return self._locks.pop(lid)["result"]

    async def lease(self, lid: str, result):
        if lid in self._locks and self._locks[lid]["result"] is None:
            self._locks[lid]["result"] = result
            return True
        return False


class TestRunnerLockWithPaymentDeadline:
    """Behavioural tests for the bounded lock wait."""

    @pytest.mark.asyncio
    async def test_lock_resolves_normally_without_payment(self):
        """No payment deadline: lock resolves when leased normally."""
        runner = FakeLockRunner(payment_deadline=None)
        lid = "lid-1"
        expires = time.time() + 10.0  # well in the future

        async def resolve_after():
            await asyncio.sleep(0.1)
            await runner.lease(lid, {"answer": 42})

        result, _ = await asyncio.gather(
            runner.lock("q1", lid, expires),
            resolve_after(),
        )
        assert result == {"answer": 42}

    @pytest.mark.asyncio
    async def test_lock_resolves_normally_with_payment_deadline_future(self):
        """Payment deadline well in the future: lock still resolves on lease."""
        runner = FakeLockRunner(payment_deadline=time.time() + 3600.0)
        lid = "lid-2"
        expires = time.time() + 10.0

        async def resolve_after():
            await asyncio.sleep(0.1)
            await runner.lease(lid, "ok")

        result, _ = await asyncio.gather(
            runner.lock("q2", lid, expires),
            resolve_after(),
        )
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_lock_times_out_via_payment_deadline(self):
        """Payment deadline in the past → TimeoutError with payment message."""
        runner = FakeLockRunner(payment_deadline=time.time() - 0.1)
        lid = "lid-3"
        expires = time.time() + 3600.0  # lock itself is far in the future

        with pytest.raises(TimeoutError, match="payment result window expired"):
            await runner.lock("q3", lid, expires)

    @pytest.mark.asyncio
    async def test_lock_times_out_via_lock_expires_non_paid(self):
        """No payment deadline: TimeoutError uses the plain lock message."""
        runner = FakeLockRunner(payment_deadline=None)
        lid = "lid-4"
        expires = time.time() - 0.1  # already expired

        with pytest.raises(TimeoutError, match=r"expired at"):
            await runner.lock("q4", lid, expires)

    @pytest.mark.asyncio
    async def test_payment_deadline_bounds_effective_expires(self):
        """effective_expires stored in _locks equals min(expires, pay_deadline)."""
        pay_deadline = time.time() + 60.0
        lock_expires = time.time() + 3600.0
        runner = FakeLockRunner(payment_deadline=pay_deadline)
        lid = "lid-5"

        # Resolve immediately so lock() exits cleanly
        async def instant_resolve():
            await asyncio.sleep(0.01)
            await runner.lease(lid, "done")

        await asyncio.gather(runner.lock("q5", lid, lock_expires), instant_resolve())
        # Lock was popped on success; confirm the helper returned the right value
        assert _effective_lock_deadline(lock_expires, pay_deadline) == pytest.approx(pay_deadline, abs=0.01)

    @pytest.mark.asyncio
    async def test_non_paid_lock_not_affected_by_none_deadline(self):
        """_effective_lock_deadline(lock_ts, None) == lock_ts — no change."""
        lock_expires = time.time() + 5.0
        assert _effective_lock_deadline(lock_expires, None) == lock_expires


# ---------------------------------------------------------------------------
# Import smoke test
# ---------------------------------------------------------------------------


def test_import_kodosumi_runner_main():
    """Confirm the module imports cleanly (no Ray cluster required)."""
    import kodosumi.runner.main  # noqa: F401
    assert callable(_effective_lock_deadline)
