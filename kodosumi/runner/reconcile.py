"""
Payment-job recovery (reconciliation).

When the Ray cluster restarts (e.g. OOM-triggered ray-head restart), all
detached Runner actors die. A Runner that was waiting in
``wait_for_funds_locked`` dies without writing a terminal status, leaving the
job frozen at DB status ``payment`` forever — the Sumi status endpoint then
reports ``awaiting_payment`` indefinitely even though funds may be locked.

This module reconciles such orphaned payment jobs. It is driven by the spooler
(see ``spooler.py``) which already detects dead runners. For each frozen
``payment`` job whose Runner is no longer alive, it:

  - reads the persisted ``blockchainIdentifier`` from the monitor DB,
  - asks Masumi for the current on-chain state,
  - and either RESUMES the job (relaunching a Runner that reuses the existing
    blockchainId — never calling ``init_payment`` again) or marks it FAILED.

Design constraints (see PAYMENT-JOB-RECOVERY-DESIGN.md):
  - Never call ``init_payment`` during recovery → no duplicate payments.
  - Only act on jobs whose Runner is verifiably dead (caller's responsibility).
  - Idempotent: once a job leaves ``payment`` status it is skipped.
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from kodosumi.const import (DB_FILE, EVENT_INPUTS, EVENT_META, EVENT_PAYMENT,
                            EVENT_STATUS, NAMESPACE, STATUS_ERROR,
                            STATUS_PAYMENT)
from kodosumi.helper import now

logger = logging.getLogger(__name__)


# On-chain states that mean "funds are locked and waiting for the result".
STATE_FUNDS_LOCKED = "FundsLocked"


def decide_action(
    onchain_state: Optional[str],
    pay_by_time_ms: Optional[int],
    submit_result_time_ms: Optional[int],
    now_ts: float,
) -> str:
    """Decide what to do with an orphaned payment job. PURE function.

    Args:
        onchain_state: Masumi ``onChainState`` (None if not yet on-chain).
        pay_by_time_ms: payment deadline in epoch milliseconds (or None).
        submit_result_time_ms: result-submission deadline in epoch ms (or None).
        now_ts: current time in epoch SECONDS.

    Returns:
        - ``"resume_locked"``: funds are locked AND the result can still be
          submitted in time → relaunch and run immediately (skip the wait).
        - ``"resume_wait"``: nothing on-chain yet but still within the payment
          window → relaunch and keep waiting for FundsLocked.
        - ``"fail"``: terminal — funds never validly locked / refunded /
          disputed, payment window expired, OR funds locked but the
          result-submission window already closed (cannot complete → don't
          burn compute).
    """
    now_ms = now_ts * 1000
    if onchain_state == STATE_FUNDS_LOCKED:
        # Customer paid. Only resume if we can still submit the result on time;
        # otherwise running the agent would waste compute for a result Masumi
        # would reject.
        if submit_result_time_ms is not None and now_ms >= submit_result_time_ms:
            return "fail"
        return "resume_locked"
    if onchain_state is None:
        # Nothing on-chain yet. Keep waiting only while still within window.
        if pay_by_time_ms is not None and now_ms < pay_by_time_ms:
            return "resume_wait"
        return "fail"
    # Any other concrete state (FundsOrDatumInvalid, RefundRequested,
    # RefundWithdrawn, Withdrawn, Disputed, ResultSubmitted, ...) is terminal
    # for a job that is still stuck in our "payment" phase.
    return "fail"


def _open_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def read_last_status(db_path: Path) -> tuple:
    """Cheap peek: return (last_status, last_status_ts) or (None, None).

    Used by the spooler sweep to filter ~thousands of DBs down to the few
    that are actually frozen at ``payment`` before doing the full read.
    """
    if not db_path.exists():
        return (None, None)
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error:
        return (None, None)
    try:
        cur = conn.execute(
            "SELECT message, timestamp FROM monitor WHERE kind = ? "
            "ORDER BY timestamp DESC, id DESC LIMIT 1", (EVENT_STATUS,))
        row = cur.fetchone()
        if row:
            return (row["message"], row["timestamp"])
        return (None, None)
    except sqlite3.Error:
        return (None, None)
    finally:
        conn.close()


def read_payment_state(db_path: Path) -> Optional[Dict[str, Any]]:
    """Read everything needed to reconcile/relaunch a payment job.

    Returns None if the DB is unreadable or has no payment-init event. The
    returned dict carries the last status plus the persisted payment context
    and the parameters required to relaunch a Runner.
    """
    if not db_path.exists():
        return None
    try:
        conn = _open_ro(db_path)
    except sqlite3.Error as exc:
        logger.debug("reconcile: cannot open %s: %s", db_path, exc)
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT message FROM monitor WHERE kind = ? "
            "ORDER BY timestamp DESC, id DESC LIMIT 1", (EVENT_STATUS,))
        row = cur.fetchone()
        last_status = row["message"] if row else None

        cur.execute(
            "SELECT MAX(timestamp) AS ts FROM monitor WHERE kind = ?",
            (EVENT_STATUS,))
        row = cur.fetchone()
        last_status_ts = row["ts"] if row and row["ts"] is not None else None

        # First payment "initialized" event holds the durable blockchainId.
        cur.execute(
            "SELECT message FROM monitor WHERE kind = ? ORDER BY timestamp ASC",
            (EVENT_PAYMENT,))
        pay_init: Optional[Dict[str, Any]] = None
        for (msg,) in cur.fetchall():
            d = _unwrap(msg)
            if d and d.get("step") == "initialized":
                pay_init = d
                break

        cur.execute(
            "SELECT message FROM monitor WHERE kind = ? "
            "ORDER BY timestamp DESC, id DESC LIMIT 1", (EVENT_META,))
        row = cur.fetchone()
        meta = _unwrap(row["message"]) if row else None

        cur.execute(
            "SELECT message FROM monitor WHERE kind = ? "
            "ORDER BY timestamp ASC, id ASC LIMIT 1", (EVENT_INPUTS,))
        row = cur.fetchone()
        inputs = _unwrap(row["message"]) if row else None
    finally:
        conn.close()

    return {
        "last_status": last_status,
        "last_status_ts": last_status_ts,
        "pay_init": pay_init,
        "meta": meta,
        "inputs": inputs,
    }


def _unwrap(message: str) -> Optional[Dict[str, Any]]:
    """Undo ``serialize()`` wrapping: {"dict": {...}} -> {...}.

    serialize() wraps a plain dict as {"dict": <dict>} and a pydantic model as
    {"<ModelName>": <dump>}. For our payment/meta/inputs events the payload is
    always a dict, so we return the inner value of the single top-level key.
    """
    try:
        outer = json.loads(message)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(outer, dict) or not outer:
        return None
    if "dict" in outer:
        inner = outer["dict"]
    else:
        # single-key wrapper (e.g. a model name) — take its value
        inner = next(iter(outer.values()))
    return inner if isinstance(inner, dict) else None


def mark_failed(db_path: Path, reason: str) -> None:
    """Write a terminal error + status row so the job stops being 'payment'.

    Mirrors the runner's own terminal writes (plain-string status/error).
    """
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    try:
        conn.execute("pragma journal_mode=wal;")
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (now(), "error", reason))
        conn.execute(
            "INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)",
            (now(), EVENT_STATUS, STATUS_ERROR))
    finally:
        conn.close()


def _build_resume_extra(pay_init: Dict[str, Any],
                        meta: Dict[str, Any],
                        funds_locked: bool) -> Dict[str, Any]:
    """Build the Runner ``extra`` for a resumed job.

    Carries over the original extra and adds ``resume_payment`` so that
    ``Runner.prepare()`` reuses the existing blockchainId instead of calling
    ``init_payment`` again (which would create a duplicate payment).
    ``funds_locked`` tells start() to skip the (now-expired) wait.
    """
    extra = dict(meta.get("extra") or {})
    extra["resume_payment"] = {
        "blockchain_identifier": pay_init.get("blockchainIdentifier"),
        "pay_data": pay_init.get("pay_data") or {},
        "network": pay_init.get("network") or extra.get("network"),
        "agentIdentifier": pay_init.get("agentIdentifier")
        or extra.get("agentIdentifier"),
        "input_hash": pay_init.get("inputHash") or extra.get("input_hash"),
        "identifier_from_purchaser": extra.get("identifier_from_purchaser"),
        "funds_locked": funds_locked,
    }
    return extra


def _relaunch_resume(fid: str, state: Dict[str, Any],
                     funds_locked: bool) -> bool:
    """Relaunch a Runner for ``fid`` in resume mode. Returns True on launch.

    Defensive against double-relaunch: skips if an actor with this fid already
    exists, and catches the name-collision error from a concurrent create.
    """
    import ray

    from kodosumi.runner.main import create_runner

    try:
        ray.get_actor(fid, namespace=NAMESPACE)
        logger.info("reconcile: actor %s alive, skip resume", fid)
        return False
    except ValueError:
        pass  # not found → safe to relaunch

    meta = state["meta"] or {}
    pay_init = state["pay_init"] or {}
    inputs = state["inputs"]
    entry_point = meta.get("entry_point")
    username = meta.get("username")
    if not entry_point or not username:
        logger.warning("reconcile: %s missing entry_point/username, cannot "
                       "resume", fid)
        return False

    extra = _build_resume_extra(pay_init, meta, funds_locked)
    try:
        _, runner = create_runner(
            username=username,
            app_url=meta.get("app_url") or "",
            entry_point=entry_point,
            inputs=inputs,
            method_info=None,
            extra=extra,
            jwt=None,
            panel_url=meta.get("panel_url") or "",
            fid=fid,
        )
        runner.run.remote()
    except ValueError as exc:
        # name already taken → another path relaunched this fid concurrently
        logger.info("reconcile: %s already relaunched (%s)", fid, exc)
        return False
    logger.info("reconcile: resumed %s (entry_point=%s, funds_locked=%s)",
                fid, entry_point, funds_locked)
    return True


async def reconcile_payment_job(
    db_path: Path,
    fid: str,
) -> str:
    """Reconcile one orphaned payment job. Caller must have verified the
    Runner is dead.

    Returns one of: ``"skip"`` (not a frozen payment job / transient),
    ``"resumed"``, ``"failed"``.
    """
    from kodosumi.config import Settings
    from kodosumi.runner.payment import MasumiClient

    state = read_payment_state(db_path)
    if not state:
        return "skip"
    if state["last_status"] != STATUS_PAYMENT:
        return "skip"  # already moved on (running/finished/error) → idempotent
    pay_init = state["pay_init"]
    if not pay_init or not pay_init.get("blockchainIdentifier"):
        return "skip"  # no payment context → nothing we can resolve

    blockchain_id = pay_init["blockchainIdentifier"]
    network = pay_init.get("network") or (state["meta"] or {}).get(
        "extra", {}).get("network")
    if not network:
        return "skip"

    try:
        masumi = MasumiClient(Settings().get_masumi(network))
        payment = await masumi.get_payment_status(blockchain_id, network)
    except Exception as exc:
        logger.warning("reconcile: Masumi lookup failed for %s: %s", fid, exc)
        return "skip"  # transient → retry next sweep

    if payment is None:
        return "skip"  # not resolvable yet → retry next sweep

    pd = pay_init.get("pay_data") or {}
    onchain_state = payment.get("onChainState")
    pay_by_time = payment.get("payByTime") or pd.get("payByTime")
    submit_result_time = (payment.get("submitResultTime")
                          or pd.get("submitResultTime"))
    pay_by_time_ms = int(pay_by_time) if pay_by_time else None
    submit_result_time_ms = int(submit_result_time) if submit_result_time else None

    action = decide_action(
        onchain_state, pay_by_time_ms, submit_result_time_ms, now())
    if action in ("resume_locked", "resume_wait"):
        funds_locked = action == "resume_locked"
        if _relaunch_resume(fid, state, funds_locked):
            return "resumed"
        return "skip"
    mark_failed(
        db_path,
        f"payment not completable (onChainState={onchain_state}); "
        f"job orphaned by cluster restart and could not be resumed")
    logger.info("reconcile: failed %s (onChainState=%s)", fid, onchain_state)
    return "failed"
