import asyncio
import logging
import os
import shutil
import socket
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Union

import psutil
import ray
from ray.actor import ActorHandle
from ray.util.state import list_actors
from ray.util.state.common import ActorState

import kodosumi.config
from kodosumi import helper
from kodosumi.const import DB_FILE, NAMESPACE, SPOOLER_NAME, STATUS_PAYMENT
from kodosumi.log import logger, spooler_logger, slog
from kodosumi.runner.reconcile import (read_last_status,
                                       reconcile_payment_job)
from kodosumi.helper import now


# ---------------------------------------------------------------------------
# D4 (#69) — sd_notify helper (12-line stdlib, no new dependency)
# ---------------------------------------------------------------------------

def _sd_notify(state: str) -> None:
    """Send a datagram to $NOTIFY_SOCKET if set; swallow all errors (no-op
    when not running under systemd or when the socket is unavailable)."""
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return
    try:
        # Support Linux abstract-namespace sockets (@name → \0name)
        if notify_socket.startswith("@"):
            addr: Union[str, bytes] = "\0" + notify_socket[1:]
        else:
            addr = notify_socket
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(state.encode(), addr)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# D5 (#74b) — spooler_attached() guard
# ---------------------------------------------------------------------------

def spooler_attached() -> bool:
    """Return True iff the SpoolerLock actor "Spooler" is reachable via Ray.

    Used by Launch() and _submit_job() to reject job creation when no spooler
    is present (which would cause silent event loss).
    """
    try:
        ray.get_actor(SPOOLER_NAME, namespace=NAMESPACE)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# D5 (#74a) — final-drain helper (extracted for testability)
# ---------------------------------------------------------------------------

def _drain_remaining(events, save_fn, batch_size: int,
                     max_iterations: int = 10000) -> int:
    """Pull all remaining events from *events* queue and persist them via
    *save_fn(batch)*.

    This is called immediately BEFORE ``ray.kill(runner)`` to ensure no
    in-RAM events are lost after the main drain loop exits.

    Args:
        events: A queue object exposing ``size() -> int`` and
            ``get_nowait_batch(n) -> list`` (Ray ActorQueue API).
        save_fn: Callable accepting a list of event dicts; persists them.
        batch_size: Max items to pull per iteration.
        max_iterations: Hard upper bound to avoid any infinite loop (generous
            default covers tens-of-thousands of buffered events).

    Returns:
        Number of additional events drained.
    """
    drained = 0
    prev_size = -1
    for _ in range(max_iterations):
        try:
            size = events.size()
        except Exception:
            # Queue actor gone — nothing left to drain.
            break
        if size == 0:
            break
        if size == prev_size:
            # No progress — guard against a queue that never empties.
            break
        prev_size = size
        try:
            batch = events.get_nowait_batch(min(batch_size, size))
        except Exception:
            # ActorDiedError or similar — queue is gone.
            break
        if batch:
            save_fn(batch)
            drained += len(batch)
    return drained


@ray.remote
class SpoolerLock:

    def __init__(self, pid: int):
        self.pid = pid
        self.active = 0
        self.total = 0

    def get_pid(self):
        return self.pid

    def get_meta(self):
        return {
            "pid": self.pid,
            "active": self.active,
            "total": self.total
        }

    def update(self, active: int, total: int):
        self.active = active
        self.total = total

class Spooler:
    def __init__(self,
                 exec_dir: Union[str, Path],
                 interval: float=1.,
                 batch_size: int=10,
                 batch_timeout: float=0.1,
                 reconcile_interval: float=600.,
                 reconcile_min_age: float=60.):
        self.exec_dir = Path(exec_dir)
        self.exec_dir.mkdir(parents=True, exist_ok=True)
        self.interval = interval
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.shutdown_event = asyncio.Event()
        self.monitor: dict = {}
        self.lock = None
        # Payment-job reconciliation: how often to sweep for orphaned
        # "payment" jobs, and how long a job must have been frozen before we
        # touch it (so freshly-started jobs are never disturbed).
        self.reconcile_interval = reconcile_interval
        self.reconcile_min_age = reconcile_min_age
        self._last_reconcile: float = 0.0
        self._reconcile_task = None

    def setup_database(self, username: str, fid: str):
        dir_path = self.exec_dir.joinpath(username, fid)
        dir_path.mkdir(parents=True, exist_ok=True)
        db_path = dir_path.joinpath(DB_FILE)
        conn = sqlite3.connect(
            str(db_path), isolation_level=None, autocommit=True)
        conn.execute('pragma journal_mode=wal;')
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS monitor (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            )
        """)
        return conn
    
    def save(self, conn: sqlite3.Connection, fid: str, payload: List[Dict]):
        if not payload:
            return
        try:
            cursor = conn.cursor()
            for val in payload:
                cursor.execute(
                    """
                    INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)
                    """, (val.get("timestamp"), val.get("kind"), val.get("payload"))
                )
                slog(logger, logging.DEBUG, "spooler.saved",
                     fid=fid, kind=val.get("kind"))
        except Exception:
            logger.critical(f"failed to save {fid}", exc_info=True)

    def _scan_frozen_payments(self, alive_fids: set) -> List[tuple]:
        """Find execution DBs frozen at status 'payment' (blocking, run in a
        thread). Returns [(db_path, fid), ...] for jobs that have no live
        Runner and have been frozen longer than ``reconcile_min_age``.
        """
        out: List[tuple] = []
        t0 = now()
        try:
            user_dirs = list(self.exec_dir.iterdir())
        except OSError:
            return out
        for user_dir in user_dirs:
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue
            try:
                exec_dirs = list(user_dir.iterdir())
            except OSError:
                continue
            for exec_dir in exec_dirs:
                fid = exec_dir.name
                if fid in alive_fids or fid.startswith("."):
                    continue
                if not exec_dir.is_dir():
                    continue
                db_path = exec_dir / DB_FILE
                status, ts = read_last_status(db_path)
                if status != STATUS_PAYMENT:
                    continue
                if ts and (t0 - ts) < self.reconcile_min_age:
                    continue  # too fresh — leave it alone
                out.append((db_path, fid))
        return out

    async def reconcile_payments(self, alive_fids: set):
        """Sweep for orphaned 'payment' jobs and resume or fail them.

        Defensive: re-checks each candidate's Runner is really dead before
        acting (the scan ran in a thread; state may have changed since).
        """
        try:
            candidates = await asyncio.to_thread(
                self._scan_frozen_payments, alive_fids)
        except Exception:
            logger.critical("reconcile sweep scan failed", exc_info=True)
            return
        if not candidates:
            return
        slog(logger, logging.INFO, "spooler.reconcile",
             candidates=len(candidates), status="sweep")
        for db_path, fid in candidates:
            try:
                ray.get_actor(fid, namespace=NAMESPACE)
                continue  # actor alive after all → skip
            except ValueError:
                pass
            except Exception:
                slog(logger, logging.WARNING, "spooler.reconcile.actor_lookup_error",
                     fid=fid, exc_info=True)
                continue
            try:
                result = await reconcile_payment_job(db_path, fid)
                if result != "skip":
                    slog(logger, logging.INFO, "spooler.reconcile",
                         fid=fid, status=result)
            except Exception:
                logger.critical(f"reconcile failed for {fid}", exc_info=True)

    async def retrieve(self, runner: ActorHandle, state: ActorState):
        if state.name is None:
            logger.critical(f"actor {state.actor_id} has no name.")
        fid: str = str(state.name)
        username = await runner.get_username.remote()
        conn = self.setup_database(username, fid)
        while True:
            done, _ = ray.wait(
                [runner.get_queue.remote()], timeout=0.01)
            if done:
                ret = await asyncio.gather(*done)
                events = ret[0]
                break
            await asyncio.sleep(0.01)
        n = 0
        try:
            while not self.shutdown_event.is_set():
                done, _ = ray.wait(
                    [runner.is_active.remote()], timeout=0.01)
                if done:
                    ret = await asyncio.gather(*done)
                    if ret:
                        if ret[0] == False:
                            break
                try:
                    batch = events.get_nowait_batch(
                        min(self.batch_size, events.size()))
                except ray.exceptions.ActorDiedError:
                    # Queue actor died - runner has finished, exit gracefully
                    logger.debug(f"queue actor died for {fid}, exiting retrieval")
                    break
                if batch:
                    self.save(conn, fid, batch)
                    slog(logger, logging.DEBUG, "spooler.saved",
                         fid=fid, records=len(batch))
                    n += len(batch)
                await asyncio.sleep(0.01)
            # --- D5 (#74a) FINAL DRAIN: pull every remaining event from the
            # in-RAM queue before killing the runner actor so no events are
            # lost.  _drain_remaining() is bounded by max_iterations (default
            # 10000) and breaks on no-progress, so it cannot loop forever.
            extra = _drain_remaining(
                events,
                lambda batch: self.save(conn, fid, batch),
                self.batch_size,
            )
            if extra:
                slog(logger, logging.INFO, "spooler.drain",
                     fid=fid, extra_records=extra)
                n += extra
            ray.kill(runner)
            slog(logger, logging.INFO, "spooler.finished",
                 fid=fid, status="finished", records=n)
        except Exception as e:
            logger.critical(
                f"failed to retrieve from {fid} after {n} records",
                exc_info=True)
        finally:
            conn.close()

    async def start(self):
        try:
            state = ray.get_actor("Spooler", namespace=NAMESPACE)
            ray.get_actor("Spooler", namespace=NAMESPACE)
            objref = state.get_pid.remote()
            pid = ray.get(objref)
            logger.warning(f"spooler already running, pid={pid}. Exiting.")
            return
        except Exception:
            # No prior "Spooler" actor exists (the normal first-start path) →
            # fall through and create the SpoolerLock below.
            pass
        self.lock = SpoolerLock.options(
            name="Spooler",
            namespace=NAMESPACE).remote(pid=os.getpid())
        pid = await self.lock.get_pid.remote()
        logger.info(f"exec source path {self.exec_dir}")
        slog(logger, logging.INFO, "spooler.started", pid=pid)
        # D4 (#69): notify systemd that we are ready (READY=1).
        # No-op when $NOTIFY_SOCKET is absent (dev/non-systemd environments).
        _sd_notify("READY=1")
        total = 0
        progress = """|/-\\|/-\\"""
        p = 0
        while not self.shutdown_event.is_set():
            # D4 (#69): heartbeat so systemd watchdog knows we are alive.
            _sd_notify("WATCHDOG=1")
            # D5 (#74c): SpoolerLock recreation — if the head node restarted
            # while this process kept running the "Spooler" actor may be gone.
            # Recreate it so /health and spooler_attached() reflect reality.
            if self.lock is not None:
                try:
                    ray.get_actor(SPOOLER_NAME, namespace=NAMESPACE)
                except Exception:
                    try:
                        self.lock = SpoolerLock.options(
                            name=SPOOLER_NAME,
                            namespace=NAMESPACE).remote(pid=os.getpid())
                        slog(logger, logging.INFO, "spooler.lock_recreated",
                             pid=os.getpid())
                    except Exception:
                        logger.warning(
                            "failed to recreate SpoolerLock actor",
                            exc_info=True)
            try:
                states = list_actors(filters=[
                    ("class_name", "=", "Runner"), 
                    ("state", "=", "ALIVE")])
            except Exception as e:
                logger.critical(f"failed listing names actors", exc_info=True)
                states = []
            for state in states:
                if state.name not in self.monitor:
                    try:
                        runner = ray.get_actor(state.name, namespace=NAMESPACE)
                        task = asyncio.create_task(self.retrieve(runner, state))
                        self.monitor[state.name] = task
                        slog(logger, logging.INFO, "spooler.streaming",
                             fid=state.name)
                        total += 1
                        self.lock.update.remote(len(self.monitor), total)
                    except Exception as e:
                        logger.critical(
                            f"failed to stream {state.name}", exc_info=True)
            dead = [name for name, task in self.monitor.items() if task.done()]
            if dead:
                for state in dead:
                    del self.monitor[state]
                self.lock.update.remote(len(self.monitor), total)
            # Reconcile orphaned "payment" jobs (runner died on cluster restart
            # → frozen at awaiting_payment). Runs once on startup (since
            # _last_reconcile=0) and then every reconcile_interval seconds, in
            # the background so the main loop is never blocked.
            if (now() - self._last_reconcile >= self.reconcile_interval
                    and (self._reconcile_task is None
                         or self._reconcile_task.done())):
                self._last_reconcile = now()
                alive_fids = {s.name for s in states if s.name}
                self._reconcile_task = asyncio.create_task(
                    self.reconcile_payments(alive_fids))
            if sys.stdout.isatty():
                print(f"{progress[p]} Actors active ({len(self.monitor)}) - "
                    f"total: ({total})", " "*20, end="\r", flush=True)
                p = 0 if p >= len(progress) - 1 else p + 1
            await asyncio.sleep(self.interval)

    async def shutdown(self):
        logger.info(f"spooler shutdown, please wait.")
        self.shutdown_event.set()
        #ray.kill(self.lock)
        await asyncio.gather(*self.monitor.values())


def cleanup(settings: kodosumi.config.Settings):
    upload_dir = Path(settings.UPLOAD_DIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    for upload in upload_dir.iterdir():
        if upload.is_dir():
            logger.info(f"cleanup {upload}")
            shutil.rmtree(upload)

def main(settings: kodosumi.config.Settings):
    cleanup(settings)
    spooler = Spooler(
        exec_dir=settings.EXEC_DIR, 
        interval=settings.SPOOLER_INTERVAL, 
        batch_size=settings.SPOOLER_BATCH_SIZE, 
        batch_timeout=settings.SPOOLER_BATCH_TIMEOUT)
    try:
        spooler_logger(settings)
        helper.ray_init(settings)
        asyncio.run(spooler.start())
    finally:
        asyncio.run(spooler.shutdown())
        helper.ray_shutdown()

def terminate(settings: kodosumi.config.Settings):
    spooler_logger(settings)
    helper.ray_init(settings)
    try:
        state = ray.get_actor(SPOOLER_NAME, namespace=NAMESPACE)
        objref = state.get_pid.remote()
        pid = ray.get(objref)
        proc = psutil.Process(pid)
        proc.terminate()
        logger.warning(f"spooler stopped with pid={pid}")
    except psutil.NoSuchProcess:
        logger.critical(f"no spooler found with pid={pid}")
    except Exception:
        logger.warning("no spooler found")


def run():
    main(kodosumi.config.Settings())


if __name__ == "__main__":
    run()
