import asyncio
import os
import sys
from pathlib import Path

import aiofiles
import ray
from ray.util.state import list_actors

import kodosumi.config
from kodosumi import helper
from kodosumi.log import logger, spooler_logger
from kodosumi.runner import (NAMESPACE, STATUS_FINAL, EVENT_STATUS, 
                             STATUS_RUNNING)
import string


SLEEP_LONG = 0.25
SLEEP_SHORT = 0.01
FINISH_TIMEOUT = 20
EVENT_LOG_FILE = "event.log"
PICKLE_FILE = "cache.pickle"
STDERR_FILE = "stderr.log"
STDOUT_FILE = "stdout.log"


@ray.remote
class Spooler:

    async def ready(self):
        return True

def finished(name, timestamp, key, data):
    return key == EVENT_STATUS and data[1:] in STATUS_FINAL


def running(name, timestamp, key, data):
    return key == EVENT_STATUS and data[1:] == STATUS_RUNNING


async def save(exec_dir, user, name, timestamp, key, data):
    folder = exec_dir.joinpath(user, name)
    folder.mkdir(parents=True, exist_ok=True)
    parent = folder.joinpath
    filename = parent(EVENT_LOG_FILE)
    dump = f"{timestamp} {helper.now().isoformat()} {key} {data}"
    logger.debug(f"{user}/{name[-6:]} {key} {data}")
    fh = await aiofiles.open(filename, "a")
    await fh.write(f"{dump}\n")
    await fh.close()


async def loop(settings: kodosumi.config.Settings):

    i = 0
    progress = """|/-\\|/-\\"""
    exec_dir = Path(settings.EXEC_DIR)
    actor_memo = {}
    logger.info(f"Process ID {os.getpid()}")
    lock = Spooler.options(name="Spooler").remote()  # type: ignore
    assert await lock.ready.remote()
    last = -1
    while True:
        try:
            wait = True
            states = list_actors(filters=[
                ("class_name", "=", "Runner"), ("state", "=", "ALIVE")])
            for state in states:
                actor = ray.get_actor(state.name, namespace=NAMESPACE)
                if state.name not in actor_memo:
                    user = ray.get(actor.get_user.remote())
                    if user is None:
                        continue
                    actor_memo[state.name] = user
                user = actor_memo[state.name]
                ready, _ = ray.wait([actor.async_dequeue.remote()])
                if ready:
                    ret = await asyncio.gather(*ready)
                    for r in ret:
                        if not r:
                            continue
                        try: await save(exec_dir, user, state.name, *r)
                        except TypeError as exc:
                            logger.critical(
                                f"{user}/{state.name[-6:]}: {exc}", exc_info=exc)
                        if running(state.name, *r):
                            entry_point = await actor.get_entry_point.remote()
                            logger.info(
                                f"{user}/{state.name[-6:]} started at {entry_point}")
                        if finished(state.name, *r):
                            entry_point = await actor.get_entry_point.remote()
                            ready, _ = ray.wait(
                                [actor.stop.remote()], timeout=FINISH_TIMEOUT)
                            if not ready:
                                logger.error(
                                    f"{user}/{state.name[-6:]} {entry_point} finish failed")
                            else:
                                await asyncio.gather(*ready)
                                logger.info(
                                    f"{user}/{state.name[-6:]} {entry_point} finished")
                            del actor_memo[state.name]
                            ray.kill(actor)
                        wait = False
            if wait:
                await asyncio.sleep(SLEEP_LONG)
                end = "       "
            else:
                await asyncio.sleep(SLEEP_SHORT)
                end = " ...   "
            size = len(states)
            if sys.stdout.isatty():
                print(progress[i], "active actors:", size, 
                      end=end + "\r", flush=True)
                i = 0 if i >= len(progress) - 1 else i + 1
            else:
                if size != last:
                    logger.info(f"active actors: {size}")
                last = size
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            logger.critical(f"failed while spooling", exc_info=exc)
    
def main(settings: kodosumi.config.Settings):

    helper.ray_init(settings)
    spooler_logger(settings)
    logger.info(f"Ray server at {settings.RAY_SERVER}")
    states = list_actors(filters=[("class_name", "=", "Spooler"),
                                  ("state", "=", "ALIVE")])
    if states:
        logger.warning("Spooler is already running.")
        sys.exit(1)
    try:
        asyncio.run(loop(settings))
    except KeyboardInterrupt:
        logger.warning("Keyboard interrupt")
    finally:
        ray.shutdown()


if __name__ == "__main__":
    main(kodosumi.config.Settings())