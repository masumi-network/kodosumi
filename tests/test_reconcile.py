"""
Tests for payment-job recovery (kodosumi/runner/reconcile.py), the
Runner.prepare() resume path, and the Runner._await_payment() wait-skip.

No real Ray cluster or Masumi network is used — SQLite is real (temp files),
Masumi/Settings/relaunch are stubbed. Run only this file:

    pytest tests/test_reconcile.py -v
"""
import asyncio
import json
import sqlite3
import types

from kodosumi.helper import now, serialize
from kodosumi.runner import reconcile
from kodosumi.runner.reconcile import (decide_action, mark_failed,
                                       read_last_status, read_payment_state,
                                       reconcile_payment_job)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _make_db(tmp_path, *, last_status="payment", with_payment=True,
             blockchain_id="bc-abc-123", network="Preprod",
             pay_by_time_ms=None, submit_result_time_ms=None,
             entry_point="mymod:agent", username="user-1"):
    """Create a realistic monitor.db for one execution and return its Path."""
    db = tmp_path / "sqlite3.db"
    conn = sqlite3.connect(str(db), isolation_level=None)
    conn.execute("""
        CREATE TABLE monitor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            kind TEXT NOT NULL,
            message TEXT NOT NULL)
    """)
    t = now() - 3600  # an hour ago → older than reconcile_min_age

    def ins(kind, message):
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?,?,?)",
            (t, kind, message))

    ins("status", "starting")
    meta = {
        "fid": "6a00000000000000000000aa", "username": username,
        "app_url": "http://app", "panel_url": "http://panel",
        "entry_point": entry_point,
        "extra": {
            "sumi_endpoint": "agent/meta",
            "agentIdentifier": "agent-xyz",
            "network": network,
            "identifier_from_purchaser": "purch-1",
            "input_hash": "hash-1",
        },
    }
    ins("meta", serialize(meta))
    ins("inputs", serialize({"query": "hello"}))
    if with_payment:
        pay_data = {}
        if pay_by_time_ms is not None:
            pay_data["payByTime"] = pay_by_time_ms
        if submit_result_time_ms is not None:
            pay_data["submitResultTime"] = submit_result_time_ms
        ins("payment", serialize({
            "step": "initialized",
            "agentIdentifier": "agent-xyz",
            "network": network,
            "inputHash": "hash-1",
            "blockchainIdentifier": blockchain_id,
            "pay_data": pay_data,
        }))
    if last_status:
        ins("status", last_status)
    conn.close()
    return db


class _FakeMasumi:
    """Stub MasumiClient. Class attr `RESPONSE` is returned by status lookup."""
    RESPONSE = None

    def __init__(self, cfg):
        pass

    async def get_payment_status(self, blockchain_id, network):
        return type(self).RESPONSE


class _FakeSettings:
    def get_masumi(self, network):
        return object()


def _patch_masumi(monkeypatch, response):
    _FakeMasumi.RESPONSE = response
    monkeypatch.setattr("kodosumi.runner.payment.MasumiClient", _FakeMasumi)
    monkeypatch.setattr("kodosumi.config.Settings", _FakeSettings)


_FUTURE = lambda: (now() + 600) * 1000  # noqa: E731
_PAST = lambda: (now() - 600) * 1000    # noqa: E731


# --------------------------------------------------------------------------
# decide_action — pure logic
# --------------------------------------------------------------------------

def test_decide_funds_locked_within_submit_window_resumes():
    assert decide_action("FundsLocked", None, _FUTURE(), now()) == "resume_locked"
    # no submit deadline known → still resume (can't prove it's too late)
    assert decide_action("FundsLocked", None, None, now()) == "resume_locked"


def test_decide_funds_locked_submit_window_closed_fails():
    # paid, but result-submission window already closed → don't burn compute
    assert decide_action("FundsLocked", None, _PAST(), now()) == "fail"


def test_decide_pending_within_window_resumes_wait():
    assert decide_action(None, _FUTURE(), None, now()) == "resume_wait"


def test_decide_pending_expired_fails():
    assert decide_action(None, _PAST(), None, now()) == "fail"
    assert decide_action(None, None, None, now()) == "fail"


def test_decide_terminal_states_fail():
    for st in ("FundsOrDatumInvalid", "RefundWithdrawn", "Withdrawn",
               "RefundRequested", "Disputed", "ResultSubmitted"):
        assert decide_action(st, _FUTURE(), _FUTURE(), now()) == "fail"


# --------------------------------------------------------------------------
# DB read/write helpers — real SQLite
# --------------------------------------------------------------------------

def test_read_last_status(tmp_path):
    db = _make_db(tmp_path, last_status="payment")
    status, ts = read_last_status(db)
    assert status == "payment"
    assert ts is not None


def test_read_payment_state(tmp_path):
    db = _make_db(tmp_path, blockchain_id="bc-XYZ", network="Mainnet")
    state = read_payment_state(db)
    assert state["last_status"] == "payment"
    assert state["pay_init"]["blockchainIdentifier"] == "bc-XYZ"
    assert state["pay_init"]["network"] == "Mainnet"
    assert state["meta"]["entry_point"] == "mymod:agent"
    assert state["meta"]["username"] == "user-1"
    assert state["inputs"] == {"query": "hello"}


def test_mark_failed_writes_error_status(tmp_path):
    db = _make_db(tmp_path, last_status="payment")
    mark_failed(db, "boom")
    status, _ = read_last_status(db)
    assert status == "error"
    conn = sqlite3.connect(str(db))
    errors = conn.execute(
        "SELECT message FROM monitor WHERE kind='error'").fetchall()
    conn.close()
    assert any("boom" in r[0] for r in errors)


# --------------------------------------------------------------------------
# reconcile_payment_job — orchestration (Masumi + relaunch stubbed)
# --------------------------------------------------------------------------

def test_reconcile_funds_locked_resumes_with_funds_locked_flag(tmp_path,
                                                               monkeypatch):
    db = _make_db(tmp_path, last_status="payment")
    _patch_masumi(monkeypatch, {"onChainState": "FundsLocked",
                                "submitResultTime": _FUTURE()})
    calls = []
    monkeypatch.setattr(
        reconcile, "_relaunch_resume",
        lambda fid, state, funds_locked: calls.append(
            (fid, funds_locked)) or True)

    result = asyncio.run(reconcile_payment_job(db, "fid-1"))

    assert result == "resumed"
    # CRITICAL: funds_locked must be True so start() skips the expired wait
    assert calls == [("fid-1", True)]
    assert read_last_status(db)[0] == "payment"  # not failed


def test_reconcile_funds_locked_but_submit_expired_fails(tmp_path, monkeypatch):
    db = _make_db(tmp_path, last_status="payment")
    _patch_masumi(monkeypatch, {"onChainState": "FundsLocked",
                                "submitResultTime": _PAST()})
    monkeypatch.setattr(reconcile, "_relaunch_resume",
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError("must not resume — cannot submit")))

    result = asyncio.run(reconcile_payment_job(db, "fid-1b"))

    assert result == "failed"
    assert read_last_status(db)[0] == "error"


def test_reconcile_pending_in_window_resumes_wait(tmp_path, monkeypatch):
    db = _make_db(tmp_path, last_status="payment")
    _patch_masumi(monkeypatch, {"onChainState": None, "payByTime": _FUTURE()})
    calls = []
    monkeypatch.setattr(
        reconcile, "_relaunch_resume",
        lambda fid, state, funds_locked: calls.append(
            (fid, funds_locked)) or True)

    result = asyncio.run(reconcile_payment_job(db, "fid-1c"))

    assert result == "resumed"
    # waiting, not yet locked → must NOT skip the wait
    assert calls == [("fid-1c", False)]


def test_reconcile_refunded_marks_failed(tmp_path, monkeypatch):
    db = _make_db(tmp_path, last_status="payment")
    _patch_masumi(monkeypatch, {"onChainState": "RefundWithdrawn"})
    monkeypatch.setattr(reconcile, "_relaunch_resume",
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError("must not relaunch")))

    result = asyncio.run(reconcile_payment_job(db, "fid-2"))

    assert result == "failed"
    assert read_last_status(db)[0] == "error"


def test_reconcile_expired_window_marks_failed(tmp_path, monkeypatch):
    past = _PAST()
    db = _make_db(tmp_path, last_status="payment", pay_by_time_ms=past)
    _patch_masumi(monkeypatch, {"onChainState": None, "payByTime": past})

    result = asyncio.run(reconcile_payment_job(db, "fid-3"))

    assert result == "failed"
    assert read_last_status(db)[0] == "error"


def test_reconcile_skips_non_payment(tmp_path, monkeypatch):
    db = _make_db(tmp_path, last_status="running")
    _patch_masumi(monkeypatch, {"onChainState": "FundsLocked"})
    monkeypatch.setattr(reconcile, "_relaunch_resume",
                        lambda *a: (_ for _ in ()).throw(
                            AssertionError("must not relaunch")))

    result = asyncio.run(reconcile_payment_job(db, "fid-4"))

    assert result == "skip"
    assert read_last_status(db)[0] == "running"  # untouched


def test_reconcile_transient_masumi_none_skips(tmp_path, monkeypatch):
    db = _make_db(tmp_path, last_status="payment")
    _patch_masumi(monkeypatch, None)  # payment not resolvable right now

    result = asyncio.run(reconcile_payment_job(db, "fid-5"))

    assert result == "skip"
    assert read_last_status(db)[0] == "payment"  # left alone, retried later


# --------------------------------------------------------------------------
# Runner internals — raw (undecorated) class, no Ray
# --------------------------------------------------------------------------

def _raw_runner_class():
    from kodosumi.runner.main import Runner
    return Runner.__ray_metadata__.modified_class


# --- prepare(): no-duplicate-payment guarantee --------------------------

def test_prepare_resume_reuses_blockchain_id_without_init_payment():
    raw = _raw_runner_class()
    events = []

    async def fake_put(kind, payload):
        events.append((kind, payload))

    def must_not_call():
        raise AssertionError("get_payment_config/init_payment must NOT run "
                             "on resume — would create a duplicate payment")

    fake_self = types.SimpleNamespace(
        _payment=None,
        _payment_lock=asyncio.Lock(),
        _put_async=fake_put,
        get_payment_config=must_not_call,
        extra={
            "agentIdentifier": "agent-xyz",
            "network": "Mainnet",
            "input_hash": "hash-1",
            "identifier_from_purchaser": "purch-1",
            "resume_payment": {
                "blockchain_identifier": "bc-RESUME-999",
                "pay_data": {"payByTime": 123},
                "network": "Mainnet",
                "agentIdentifier": "agent-xyz",
                "input_hash": "hash-1",
                "identifier_from_purchaser": "purch-1",
                "funds_locked": True,
            },
        },
    )

    result = asyncio.run(raw.prepare(fake_self))

    assert result["blockchain_identifier"] == "bc-RESUME-999"
    assert result["pay_conf"]["network"] == "Mainnet"
    assert result["funds_locked"] is True  # propagated → start() skips wait
    kinds = [json.loads(p).get("dict", {}).get("step")
             for k, p in events if k == "payment"]
    assert "resumed" in kinds
    assert "initialized" not in kinds


def test_prepare_without_resume_uses_normal_path():
    """Sanity: without resume_payment, prepare() falls through to the regular
    path (get_payment_config is consulted). Proves the resume block is additive
    and does not change normal behaviour."""
    raw = _raw_runner_class()
    consulted = []

    async def fake_put(kind, payload):
        pass

    async def fake_get_payment_config():
        consulted.append(True)
        return None

    fake_self = types.SimpleNamespace(
        _payment=None,
        _payment_lock=asyncio.Lock(),
        _put_async=fake_put,
        get_payment_config=fake_get_payment_config,
        extra={},
    )

    result = asyncio.run(raw.prepare(fake_self))

    assert result is None
    assert consulted == [True]


# --- _await_payment(): the wait-skip that makes resume actually work ----

def test_await_payment_skips_wait_when_funds_locked(monkeypatch):
    """THE show-stopper guard: a resumed FundsLocked job must NOT re-enter
    wait_for_funds_locked (its payByTime is long past → would raise)."""
    raw = _raw_runner_class()
    import kodosumi.runner.main as m

    class BoomMasumi:
        def __init__(self, cfg):
            pass

        async def wait_for_funds_locked(self, **kw):
            raise AssertionError(
                "wait_for_funds_locked must be skipped when funds_locked=True")

    monkeypatch.setattr(m, "MasumiClient", BoomMasumi)
    monkeypatch.setattr(
        m, "Settings",
        lambda: types.SimpleNamespace(get_masumi=lambda n: object()))

    fake_self = types.SimpleNamespace()
    payment = {
        "funds_locked": True,
        "pay_conf": {"network": "Mainnet"},
        "blockchain_identifier": "bc",
        "pay_data": {"payByTime": _PAST()},  # expired — would blow up the wait
    }
    # must complete without raising
    asyncio.run(raw._await_payment(fake_self, payment))


def test_await_payment_waits_when_not_locked(monkeypatch):
    """Normal path: without funds_locked, _await_payment polls Masumi."""
    raw = _raw_runner_class()
    import kodosumi.runner.main as m
    calls = []

    class FakeMasumi:
        def __init__(self, cfg):
            pass

        async def wait_for_funds_locked(self, **kw):
            calls.append(kw)
            return {}

    monkeypatch.setattr(m, "MasumiClient", FakeMasumi)
    monkeypatch.setattr(
        m, "Settings",
        lambda: types.SimpleNamespace(get_masumi=lambda n: object()))

    fake_self = types.SimpleNamespace()
    payment = {
        "funds_locked": False,
        "pay_conf": {"network": "Preprod"},
        "blockchain_identifier": "bc",
        "pay_data": {"payByTime": 123},
    }
    asyncio.run(raw._await_payment(fake_self, payment))
    assert len(calls) == 1
    assert calls[0]["blockchain_identifier"] == "bc"
