import asyncio
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Union

import ray
from ray.actor import ActorHandle
from ray.util.state import list_actors
from ray.util.state.common import ActorState

import kodosumi.config
from kodosumi import helper
from kodosumi.log import logger, spooler_logger
from kodosumi.runner import NAMESPACE


DB_FILE = "sqlite3.db"


@ray.remote
class SpoolerLock:

    async def ready(self):
        return True


class Spooler:
    def __init__(self, 
                 exec_dir: Union[str, Path],
                 interval: float=5.,
                 batch_size: int=10,
                 batch_timeout: float=0.1,
                 force: bool = False):
        self.exec_dir = Path(exec_dir)
        self.exec_dir.mkdir(parents=True, exist_ok=True)
        self.interval = interval  
        self.batch_size = batch_size
        self.batch_timeout = batch_timeout
        self.force = force
        self.shutdown_event = asyncio.Event()
        self.monitor: dict = {}  
        self.lock = None

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
            values = [
                (val.get("timestamp"), val.get("kind"), val.get("payload")) 
                for val in payload
            ]
            cursor.executemany(
                """
                INSERT INTO monitor (timestamp, kind, message) VALUES (?, ?, ?)
                """, values
            )
        except Exception:
            logger.critical(f"Failed to save {fid}", exc_info=True)

    async def retrieve(self, runner: ActorHandle, state: ActorState):
        if state.name is None:
            logger.critical(f"Actor {state.actor_id} has no name.")
        fid: str = str(state.name)
        username = await runner.get_username.remote()
        conn = self.setup_database(username, fid)
        n = 0
        try:
            while not (self.shutdown_event.is_set() 
                       or not ray.get(runner.is_active.remote())):
                batch = ray.get(
                    runner.get_batch.remote(
                        self.batch_size, self.batch_timeout))
                if batch:
                    self.save(conn, fid, batch)
                    n += len(batch)
                await asyncio.sleep(0)
            ray.get(runner.shutdown.remote())
            ray.kill(runner)
            logger.info(f"Finished {fid} with {n} records")
        except Exception as e:
            logger.critical(f"Failed to retrieve from {fid}", exc_info=True)
        finally:
            conn.close()

    async def start(self):
        try:
            ray.get_actor("Spooler", namespace=NAMESPACE)
            if self.force:
                logger.warning(f"Spooler already exists. force restart.")
                state = ray.get_actor("Spooler", namespace=NAMESPACE)
                ray.kill(state)
            else:
                logger.warning(f"Spooler already exists. Exiting.")
                return
        except Exception:
            pass
        self.lock = SpoolerLock.options(
            name="Spooler", 
            lifetime="detached", 
            enable_task_events=False,
            namespace=NAMESPACE).remote()
        assert await self.lock.ready.remote()
        logger.info(f"Spooler started")
        total = 0
        progress = """|/-\\|/-\\"""
        # progress = '⡿⣟⣯⣷⣾⣽⣻⢿'
        # progress = '⠟⠯⠷⠾⠽⠻'
        # progress = '▙▛▜▟'
        # progress = '▖▘▝▗'
        # progress = ' ▁▂▃▄▅▆▇█▇▆▅▄▃▂▁'
        p = 0
        while not self.shutdown_event.is_set():
            try:
                states = list_actors(filters=[
                    ("class_name", "=", "Runner"), 
                    ("state", "=", "ALIVE")])
            except Exception as e:
                logger.critical(f"Failed listing names actors", exc_info=True)
                states = []
            for state in states:
                if state.name not in self.monitor:
                    try:
                        runner = ray.get_actor(state.name, namespace=NAMESPACE)
                        task = asyncio.create_task(self.retrieve(runner, state))
                        self.monitor[state.name] = task
                        logger.info(f"Streaming {state.name}")
                        total += 1
                    except Exception as e:
                        logger.critial(
                            f"Failed to stream {state.name}", exc_info=True)
            dead = [name for name, task in self.monitor.items() if task.done()]
            for state in dead:
                del self.monitor[state]
            if sys.stdout.isatty():
                print(f"{progress[p]} Actors active ({len(self.monitor)}) - "
                      f"total: ({total})", " "*20, end="\r", flush=True)
                p = 0 if p >= len(progress) - 1 else p + 1
            await asyncio.sleep(self.interval)
        logger.info(f"Spooler shutdown complete")

    async def shutdown(self):
        logger.info(f"Spooler shutdown, please wait.")
        self.shutdown_event.set()
        ray.kill(self.lock)
        await asyncio.gather(*self.monitor.values())


def main(settings: kodosumi.config.Settings, force: bool=False):
    spooler_logger(settings)
    helper.ray_init(settings)
    spooler = Spooler(
        exec_dir=settings.EXEC_DIR, 
        interval=settings.SPOOLER_INTERVAL, 
        batch_size=settings.SPOOLER_BATCH_SIZE, 
        batch_timeout=settings.SPOOLER_BATCH_TIMEOUT, 
        force=force)
    try:
        asyncio.run(spooler.start())
    except KeyboardInterrupt:
        asyncio.run(spooler.shutdown())
    finally:
        helper.ray_shutdown()


if __name__ == "__main__":
    main(kodosumi.config.Settings())
