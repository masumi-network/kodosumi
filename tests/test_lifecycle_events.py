"""
Tests for lifecycle events and heartbeat — tickets #70, #76, #80.

Pure unit tests: no Ray cluster, no network I/O.
Uses Runner.__ray_metadata__.modified_class to access the raw (undecorated)
Python class, and types.SimpleNamespace as fake_self stubs — the same pattern
as tests/test_reconcile.py.
"""

import asyncio
import json
import logging
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_runner():
    """Return the undecorated Runner class (bypasses @ray.remote)."""
    from kodosumi.runner.main import Runner
    return Runner.__ray_metadata__.modified_class


def _collect_slog_records(caplog, event_name: str):
    """Return caplog records whose _slog['event'] matches event_name."""
    return [
        r for r in caplog.records
        if getattr(r, "_slog", {}).get("event") == event_name
    ]


def _make_fake_self(fid: str = "test-fid") -> types.SimpleNamespace:
    """Minimal fake Runner self with stubbed _put_async."""
    events = []

    async def fake_put(kind, payload):
        events.append((kind, payload))

    return types.SimpleNamespace(
        fid=fid,
        active=True,
        _payment=None,
        _payment_deadline=None,
        _payment_lock=asyncio.Lock(),
        extra=None,
        _put_async=fake_put,
        _events=events,
    )


# ---------------------------------------------------------------------------
# Config: HEARTBEAT_INTERVAL default + env override
# ---------------------------------------------------------------------------


class TestHeartbeatIntervalConfig:
    def test_default_is_30(self):
        """Settings().HEARTBEAT_INTERVAL defaults to 30.0."""
        from kodosumi.config import Settings
        s = Settings()
        assert s.HEARTBEAT_INTERVAL == 30.0

    def test_env_override(self, monkeypatch):
        """KODO_HEARTBEAT_INTERVAL env var overrides the default."""
        monkeypatch.setenv("KODO_HEARTBEAT_INTERVAL", "60.0")
        from kodosumi.config import Settings
        s = Settings()
        assert s.HEARTBEAT_INTERVAL == 60.0

    def test_zero_disables(self, monkeypatch):
        """0 is a valid value (disables heartbeat)."""
        monkeypatch.setenv("KODO_HEARTBEAT_INTERVAL", "0")
        from kodosumi.config import Settings
        s = Settings()
        assert s.HEARTBEAT_INTERVAL == 0.0


# ---------------------------------------------------------------------------
# _heartbeat helper: emits at interval, stops on cancel
# ---------------------------------------------------------------------------


class TestHeartbeatHelper:
    @pytest.mark.asyncio
    async def test_emits_at_interval(self, caplog):
        """
        _heartbeat emits 'job.heartbeat' records approximately every
        *interval* seconds. Use a tiny interval so the test is fast.
        """
        from kodosumi.runner.main import _heartbeat

        fid = "test-fid-heartbeat-emit"
        interval = 0.05  # 50 ms

        task = asyncio.ensure_future(_heartbeat(fid, interval))
        with caplog.at_level(logging.DEBUG, logger="kodo"):
            await asyncio.sleep(interval * 3.5)
        task.cancel()
        # Allow clean cancellation
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        records = _collect_slog_records(caplog, "job.heartbeat")
        assert len(records) >= 2, (
            f"Expected ≥2 heartbeat records, got {len(records)}"
        )
        for r in records:
            assert r._slog.get("fid") == fid
            assert "elapsed_s" in r._slog

    @pytest.mark.asyncio
    async def test_stops_on_cancel(self, caplog):
        """After task.cancel() no further heartbeat records are emitted."""
        from kodosumi.runner.main import _heartbeat

        fid = "test-fid-heartbeat-cancel"
        interval = 0.05

        task = asyncio.ensure_future(_heartbeat(fid, interval))
        with caplog.at_level(logging.DEBUG, logger="kodo"):
            await asyncio.sleep(interval * 1.5)
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        count_before = len(_collect_slog_records(caplog, "job.heartbeat"))
        await asyncio.sleep(interval * 2)
        count_after = len(_collect_slog_records(caplog, "job.heartbeat"))
        assert count_after == count_before, (
            "Heartbeat kept emitting after cancel"
        )

    @pytest.mark.asyncio
    async def test_zero_interval_emits_nothing(self, caplog):
        """interval=0 returns immediately without emitting any record."""
        from kodosumi.runner.main import _heartbeat

        with caplog.at_level(logging.DEBUG, logger="kodo"):
            task = asyncio.ensure_future(_heartbeat("any-fid", 0))
            await asyncio.sleep(0.1)

        records = _collect_slog_records(caplog, "job.heartbeat")
        assert records == [], "Expected no heartbeat records for interval=0"
        assert task.done()

    @pytest.mark.asyncio
    async def test_elapsed_s_is_int(self, caplog):
        """elapsed_s field is an int (truncated, not float)."""
        from kodosumi.runner.main import _heartbeat

        interval = 0.05
        task = asyncio.ensure_future(_heartbeat("fid-elapsed", interval))
        with caplog.at_level(logging.DEBUG, logger="kodo"):
            await asyncio.sleep(interval * 1.5)
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=0.2)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        records = _collect_slog_records(caplog, "job.heartbeat")
        for r in records:
            assert isinstance(r._slog["elapsed_s"], int)


# ---------------------------------------------------------------------------
# Runner.run() lifecycle slogs: job.created / job.finished / job.failed
# Uses Runner.__ray_metadata__.modified_class + fake_self (SimpleNamespace).
# ---------------------------------------------------------------------------


class TestRunnerLifecycleSlogs:

    @pytest.mark.asyncio
    async def test_job_created_emitted(self, caplog):
        """run() emits job.created with status=running at the very start."""
        raw = _raw_runner()
        fake_self = _make_fake_self("lifecycle-created-fid")

        async def _fake_start(self_):
            pass

        async def _fake_shutdown(self_):
            self_.active = False

        fake_self.start = lambda: _fake_start(fake_self)
        fake_self.shutdown = lambda: _fake_shutdown(fake_self)

        with patch("kodosumi.runner.main.Settings") as mock_cfg:
            mock_cfg.return_value.HEARTBEAT_INTERVAL = 0.0
            with caplog.at_level(logging.INFO, logger="kodo"):
                await raw.run(fake_self)

        records = _collect_slog_records(caplog, "job.created")
        assert len(records) == 1
        r = records[0]
        assert r._slog.get("fid") == "lifecycle-created-fid"
        assert r._slog.get("status") == "running"

    @pytest.mark.asyncio
    async def test_job_finished_emitted_on_success(self, caplog):
        """run() emits job.finished with status=finished and duration_ms on success."""
        raw = _raw_runner()
        fake_self = _make_fake_self("lifecycle-finished-fid")

        async def _fake_start(self_):
            pass

        async def _fake_shutdown(self_):
            self_.active = False

        fake_self.start = lambda: _fake_start(fake_self)
        fake_self.shutdown = lambda: _fake_shutdown(fake_self)

        with patch("kodosumi.runner.main.Settings") as mock_cfg:
            mock_cfg.return_value.HEARTBEAT_INTERVAL = 0.0
            with caplog.at_level(logging.INFO, logger="kodo"):
                await raw.run(fake_self)

        records = _collect_slog_records(caplog, "job.finished")
        assert len(records) == 1
        r = records[0]
        assert r._slog.get("fid") == "lifecycle-finished-fid"
        assert r._slog.get("status") == "finished"
        assert r._slog.get("duration_ms") is not None
        assert r._slog["duration_ms"] >= 0

    @pytest.mark.asyncio
    async def test_job_failed_emitted_on_exception(self, caplog):
        """run() emits job.failed (not job.finished) when start() raises."""
        raw = _raw_runner()
        fake_self = _make_fake_self("lifecycle-failed-fid")

        async def _raise_start():
            raise RuntimeError("intentional test failure")

        async def _fake_shutdown(self_):
            self_.active = False

        fake_self.start = _raise_start
        fake_self.shutdown = lambda: _fake_shutdown(fake_self)

        with patch("kodosumi.runner.main.Settings") as mock_cfg:
            mock_cfg.return_value.HEARTBEAT_INTERVAL = 0.0
            with caplog.at_level(logging.WARNING, logger="kodo"):
                await raw.run(fake_self)

        failed = _collect_slog_records(caplog, "job.failed")
        finished = _collect_slog_records(caplog, "job.finished")

        assert len(failed) == 1, f"Expected 1 job.failed, got {len(failed)}"
        assert len(finished) == 0, "job.finished must NOT emit on exception"

        r = failed[0]
        assert r._slog.get("fid") == "lifecycle-failed-fid"
        assert r._slog.get("status") == "error"
        assert r._slog.get("duration_ms") is not None

    @pytest.mark.asyncio
    async def test_heartbeat_cancelled_in_finally(self):
        """
        Heartbeat task is cancelled in run()'s finally block.
        Verify this by using a long interval (99s) and confirming run()
        completes quickly (no hang), meaning the task was cancelled.
        """
        raw = _raw_runner()
        fake_self = _make_fake_self("lifecycle-hb-cancel-fid")

        async def _fake_start():
            pass

        async def _fake_shutdown(self_):
            self_.active = False

        fake_self.start = _fake_start
        fake_self.shutdown = lambda: _fake_shutdown(fake_self)

        import time as _time
        t0 = _time.monotonic()
        with patch("kodosumi.runner.main.Settings") as mock_cfg:
            mock_cfg.return_value.HEARTBEAT_INTERVAL = 99.0
            # If heartbeat task is NOT cancelled, run() would block for 99s+
            await asyncio.wait_for(raw.run(fake_self), timeout=5.0)
        elapsed = _time.monotonic() - t0
        # Should complete well under the 99s heartbeat interval
        assert elapsed < 5.0, (
            f"run() took {elapsed:.1f}s — heartbeat task may not have been cancelled"
        )


# ---------------------------------------------------------------------------
# #70 correlation: sumi.job_created slog in _submit_job
# ---------------------------------------------------------------------------


class TestJobCreatedCorrelationLog:
    """
    Verify sumi.job_created is emitted after fid and input_hash are available.
    Mocks proxy_forward and Ray actor.
    """

    @pytest.mark.asyncio
    async def test_job_created_slog_emitted(self, caplog):
        """
        _submit_job emits sumi.job_created with fid, agent, sokosumi_job,
        input_hash, and blockchain_identifier.
        """
        from kodosumi.service.sumi.control import _submit_job
        from kodosumi.service.sumi.models import StartJobRequest
        from kodosumi.service.expose.models import ExposeMeta

        fid = "test-sumi-fid-001"
        identifier_from_purchaser = "sokosumi-test-job"

        meta = MagicMock(spec=ExposeMeta)
        meta.url = "/test-expose/run"
        meta.data = None

        data = MagicMock(spec=StartJobRequest)
        data.identifier_from_purchaser = identifier_from_purchaser
        data.input_data = {"key": "value"}

        request = MagicMock()
        request.user = "test-user"
        request.headers = {}
        request.cookies = {}

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.headers = {"X-Kodosumi-Launch": fid}
        fake_resp.json.return_value = {"fid": fid}
        fake_resp.content = b'{"fid": "' + fid.encode() + b'"}'

        runner_actor = MagicMock()
        runner_actor.prepare = MagicMock()
        runner_actor.prepare.remote = MagicMock(return_value=None)

        with patch("kodosumi.service.sumi.control.proxy_forward",
                   AsyncMock(return_value=fake_resp)), \
             patch("kodosumi.service.sumi.control.spooler_attached",
                   return_value=True), \
             patch("kodosumi.service.sumi.control._fetch_input_schema",
                   AsyncMock(return_value={})), \
             patch("kodosumi.service.sumi.control.convert_mip003_indices_to_values",
                   side_effect=lambda d, s: d), \
             patch("kodosumi.service.sumi.control._format_service_id",
                   return_value="test-expose/run"), \
             patch("kodosumi.service.sumi.control._parse_meta_data",
                   return_value={}), \
             patch("kodosumi.service.sumi.control.asyncio.to_thread",
                   AsyncMock(return_value=runner_actor)), \
             patch("ray.get", return_value=None):
            with caplog.at_level(logging.INFO, logger="kodo"):
                result = await _submit_job(
                    expose_name="test-expose",
                    meta_name="run",
                    meta=meta,
                    network="Preprod",
                    data=data,
                    app_server="http://localhost:3370",
                    ray_serve_address="http://localhost:8005",
                    request=request,
                    state=None,
                )

        records = _collect_slog_records(caplog, "sumi.job_created")
        assert len(records) == 1, (
            f"Expected 1 sumi.job_created record, got {len(records)}: "
            f"{[r.getMessage() for r in caplog.records]}"
        )
        r = records[0]
        assert r._slog.get("fid") == fid
        assert r._slog.get("sokosumi_job") == identifier_from_purchaser
        assert r._slog.get("agent") == "test-expose/run"
        assert "input_hash" in r._slog

    @pytest.mark.asyncio
    async def test_job_created_free_agent_no_blockchain(self, caplog):
        """
        For a free (non-paid) agent with no identifier_from_purchaser,
        blockchain_identifier is '-' and sokosumi_job is '-' in the slog.
        """
        from kodosumi.service.sumi.control import _submit_job
        from kodosumi.service.sumi.models import StartJobRequest
        from kodosumi.service.expose.models import ExposeMeta

        fid = "test-free-fid-002"

        meta = MagicMock(spec=ExposeMeta)
        meta.url = "/free-expose/run"
        meta.data = None

        data = MagicMock(spec=StartJobRequest)
        data.identifier_from_purchaser = None
        data.input_data = {}

        request = MagicMock()
        request.user = "anon"
        request.headers = {}
        request.cookies = {}

        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.headers = {"X-Kodosumi-Launch": fid}
        fake_resp.json.return_value = {"fid": fid}
        fake_resp.content = b'{"fid": "' + fid.encode() + b'"}'

        runner_actor = MagicMock()
        runner_actor.prepare = MagicMock()
        runner_actor.prepare.remote = MagicMock(return_value=None)

        with patch("kodosumi.service.sumi.control.proxy_forward",
                   AsyncMock(return_value=fake_resp)), \
             patch("kodosumi.service.sumi.control.spooler_attached",
                   return_value=True), \
             patch("kodosumi.service.sumi.control._fetch_input_schema",
                   AsyncMock(return_value={})), \
             patch("kodosumi.service.sumi.control.convert_mip003_indices_to_values",
                   side_effect=lambda d, s: d), \
             patch("kodosumi.service.sumi.control._format_service_id",
                   return_value="free-expose/run"), \
             patch("kodosumi.service.sumi.control._parse_meta_data",
                   return_value={}), \
             patch("kodosumi.service.sumi.control.asyncio.to_thread",
                   AsyncMock(return_value=runner_actor)), \
             patch("ray.get", return_value=None):
            with caplog.at_level(logging.INFO, logger="kodo"):
                await _submit_job(
                    expose_name="free-expose",
                    meta_name="run",
                    meta=meta,
                    network="Preprod",
                    data=data,
                    app_server="http://localhost:3370",
                    ray_serve_address="http://localhost:8005",
                    request=request,
                    state=None,
                )

        records = _collect_slog_records(caplog, "sumi.job_created")
        assert len(records) == 1
        assert records[0]._slog.get("blockchain_identifier") == "-"
        assert records[0]._slog.get("sokosumi_job") == "-"


# ---------------------------------------------------------------------------
# #80: payment.init slog in prepare()
# Uses Runner.__ray_metadata__.modified_class + SimpleNamespace fake_self.
# ---------------------------------------------------------------------------


class TestPaymentInitLog:
    """
    Verify payment.init is emitted in Runner.prepare() after
    blockchainIdentifier is confirmed.
    """

    def _make_fake_masumi_client(self, fake_pay_resp: dict):
        """Return a fake MasumiClient class whose init_payment returns fake_pay_resp."""
        class _FakeMasumi:
            def __init__(self, cfg):
                pass

            async def init_payment(self, **kwargs):
                return fake_pay_resp

        return _FakeMasumi

    def _make_fake_settings(self):
        """Return a fake Settings class with a trivial get_masumi."""
        from kodosumi.config import MasumiConfig

        class _FakeSettings:
            def get_masumi(self, network):
                return MasumiConfig(
                    network=network,
                    base_url="http://fake-masumi",
                    token="fake-token",
                )

        return _FakeSettings

    @pytest.mark.asyncio
    async def test_payment_init_slog_emitted(self, caplog):
        """
        prepare() emits payment.init with fid, blockchain_identifier, network
        when init_payment succeeds.
        """
        import kodosumi.runner.main as main_mod
        raw = _raw_runner()

        pay_conf = {
            "agentIdentifier": "agent:abc123",
            "network": "Preprod",
            "identifier_from_purchaser": "purchaser-001",
            "input_hash": "hash-abc",
        }

        async def _fake_get_payment_config():
            return pay_conf

        fake_self = types.SimpleNamespace(
            fid="test-pay-fid",
            _payment=None,
            _payment_deadline=None,
            _payment_lock=asyncio.Lock(),
            extra={},
            _put_async=AsyncMock(),
            get_payment_config=_fake_get_payment_config,
        )

        fake_pay_resp = {
            "data": {
                "blockchainIdentifier": "blockchain-xyz-999",
                "payByTime": "1700000000000",
                "submitResultTime": "1700003600000",
            }
        }

        with patch.object(main_mod, "Settings", self._make_fake_settings()), \
             patch("kodosumi.runner.main.MasumiClient",
                   self._make_fake_masumi_client(fake_pay_resp)):
            with caplog.at_level(logging.INFO, logger="kodo"):
                result = await raw.prepare(fake_self)

        assert result is not None
        assert result["blockchain_identifier"] == "blockchain-xyz-999"

        records = _collect_slog_records(caplog, "payment.init")
        assert len(records) == 1, (
            f"Expected 1 payment.init record, got {len(records)}"
        )
        r = records[0]
        assert r._slog.get("fid") == "test-pay-fid"
        assert r._slog.get("blockchain_identifier") == "blockchain-xyz-999"
        assert r._slog.get("network") == "Preprod"

    @pytest.mark.asyncio
    async def test_payment_init_not_emitted_for_free_job(self, caplog):
        """No payment.init slog for a job with no agentIdentifier (free agent)."""
        raw = _raw_runner()

        async def _no_payment():
            return None

        fake_self = types.SimpleNamespace(
            fid="test-free-fid",
            _payment=None,
            _payment_deadline=None,
            _payment_lock=asyncio.Lock(),
            extra={},
            _put_async=AsyncMock(),
            get_payment_config=_no_payment,
        )

        with caplog.at_level(logging.INFO, logger="kodo"):
            result = await raw.prepare(fake_self)

        assert result is None
        records = _collect_slog_records(caplog, "payment.init")
        assert records == [], "payment.init must not be emitted for free jobs"

    @pytest.mark.asyncio
    async def test_payment_init_idempotent_no_duplicate_slog(self, caplog):
        """prepare() called twice returns cached result; payment.init emitted only once."""
        import kodosumi.runner.main as main_mod
        raw = _raw_runner()

        pay_conf = {
            "agentIdentifier": "agent:abc",
            "network": "Preprod",
            "identifier_from_purchaser": "purchaser-idem",
            "input_hash": "hash-idem",
        }

        async def _fake_get_payment_config():
            return pay_conf

        fake_self = types.SimpleNamespace(
            fid="test-idem-fid",
            _payment=None,
            _payment_deadline=None,
            _payment_lock=asyncio.Lock(),
            extra={},
            _put_async=AsyncMock(),
            get_payment_config=_fake_get_payment_config,
        )

        fake_pay_resp = {
            "data": {
                "blockchainIdentifier": "bc-idem-111",
                "payByTime": "1700000000000",
            }
        }

        with patch.object(main_mod, "Settings", self._make_fake_settings()), \
             patch("kodosumi.runner.main.MasumiClient",
                   self._make_fake_masumi_client(fake_pay_resp)):
            with caplog.at_level(logging.INFO, logger="kodo"):
                r1 = await raw.prepare(fake_self)
                r2 = await raw.prepare(fake_self)

        assert r1 is r2, "prepare() must be idempotent (same object returned)"
        records = _collect_slog_records(caplog, "payment.init")
        assert len(records) == 1, (
            f"payment.init must be emitted only once; got {len(records)}"
        )
