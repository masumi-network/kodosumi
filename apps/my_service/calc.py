import time
from random import randint
import datetime

import asyncio
import ray
from kodosumi.core import Tracer


@ray.remote
def calculate(i: int):
    # do your heavy-lifting work here
    t0 = datetime.datetime.now()
    s = randint(1, 3)
    time.sleep(s)
    t1 = datetime.datetime.now()
    return {
        "i": i, 
        "t0": t0, 
        "t1": t1, 
        "delta": (t1 - t0).total_seconds(), 
        "result": s
    }


async def execute(inputs: dict, tracer: Tracer):
    tasks = inputs["tasks"]
    futures = [calculate.remote(i) for i in range(tasks)]
    pending = futures.copy()
    total = []
    while True:
        done, pending = ray.wait(pending, timeout=0.01)
        if done:
            ret = await asyncio.gather(*done)
            total.extend(ret)
            await tracer.markdown(f"have results `{ret}`")
            print(f"have result `{len(total)/tasks*100:.2f}%`")
        if not pending:
            break
        await asyncio.sleep(0.1)
    return total
