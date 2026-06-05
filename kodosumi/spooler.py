import asyncio
import os
import shutil
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
from kodosumi.log import logger, spooler_logger
from kodosumi.runner.reconcile import (read_last_status,
                                       reconcile_payment_job)
from kodosumi.helper import now


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
                logger.debug(f"saved {val.get('kind')}: {val} for {fid}")
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
        logger.info(f"reconcile sweep: {len(candidates)} frozen payment job(s)")
        for db_path, fid in candidates:
            try:
                ray.get_actor(fid, namespace=NAMESPACE)
                continue  # actor alive after all → skip
            except ValueError:
                pass
            except Exception:
                continue
            try:
                result = await reconcile_payment_job(db_path, fid)
                if result != "skip":
                    logger.info(f"reconcile {fid}: {result}")
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
                    logger.debug(f"saved {len(batch)} records for {fid}")
                    n += len(batch)
                await asyncio.sleep(0.01)
            ray.kill(runner)
            logger.info(f"finished {fid} with {n} records")
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
            pass
        self.lock = SpoolerLock.options(
            name="Spooler",
            namespace=NAMESPACE).remote(pid=os.getpid())
        pid = await self.lock.get_pid.remote()
        logger.info(f"exec source path {self.exec_dir}")
        logger.info(f"spooler started, pid={pid}")
        total = 0
        progress = """|/-\\|/-\\"""
        p = 0
        while not self.shutdown_event.is_set():
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
                        logger.info(f"streaming {state.name}")
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
