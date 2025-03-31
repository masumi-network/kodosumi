import asyncio
import ray
import os
import sys

from kodosumi import response
from kodosumi.core import Tracer


@ray.remote
def hello(_id: int, tracer: Tracer):
    ctx = ray.get_runtime_context()
    jid = ctx.get_job_id()
    nid = ctx.get_node_id()
    tid = ctx.get_task_id()
    pid = os.getpid()
    tracer.init()
    print(f"hello {_id} from sys.stdout")
    sys.stderr.write(f"hello {_id} from sys.stderr\n")
    tracer.debug_sync(f"hello debug from {_id}")
    return f"Hello from {_id}: " \
           f"jid={jid}, " \
           f"nid={nid}, " \
           f"tid={tid}, " \
           f"pid={pid}"


async def execute(inputs: dict, tracer: Tracer) -> list:
    futures = [hello.remote(i, tracer) for i in range(inputs["n"])]
    all_results = await asyncio.gather(*futures)
    await tracer.markdown(f"received `{all_results}`")
    await tracer.markdown(f"\n**Found total of {len(all_results)}**")
    return response.Text(all_results)