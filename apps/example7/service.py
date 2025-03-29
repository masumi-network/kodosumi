import os
import sys
from pathlib import Path

import ray
import uvicorn
from fastapi import Form, Request, Response
from fastapi.responses import HTMLResponse
from ray import serve

from kodosumi import response
from kodosumi.core import Launch, ServeAPI, Templates, Tracer

app = ServeAPI()

templates = Templates(
    directory=Path(__file__).parent.joinpath("templates"))

@ray.remote
def hello(_id: int, tracer: Tracer):
    ctx = ray.get_runtime_context()
    jid = ctx.get_job_id()
    nid = ctx.get_node_id()
    tid = ctx.get_task_id()
    pid = os.getpid()
    tracer.init()
    tracer.debug_sync(f"hello debug from {_id}")
    return f"Hello from {_id}: " \
           f"jid={jid}, " \
           f"nid={nid}, " \
           f"tid={tid}, " \
           f"pid={pid}"

async def execute(inputs: dict, tracer: Tracer) -> list:
    futures = [hello.remote(i, tracer) for i in range(inputs["n"])]
    all_results = []
    while futures:
        done_id, futures = ray.wait(futures)
        result = ray.get(done_id[0])
        all_results.append(result)
        await tracer.markdown(f"* received {result}")
    await tracer.markdown(f"\n**Found total of {len(all_results)}**")
    return response.Text(all_results)


@app.get("/", summary="Cluster Status",
            description="Say Hello to nodes in the cluster.",
            version="1.0.0",
            author="m.rau@house-of-communication.com",
            tags=["Test"],
            entry=True)
async def get(request: Request) -> HTMLResponse:
    resp = templates.TemplateResponse(
        request=request, name="cluster_status.html", context={})
    return resp

@app.post("/", entry=True)
async def post(request: Request, 
                n: str =Form()) -> Response:
    return Launch(request, 
                  "apps.example7.service:execute", 
                  inputs={"n": int(n)}, 
                  reference=get)


@serve.deployment
@serve.ingress(app)
class Example1: pass


fast_app = Example1.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example7.service:app", 
                host="0.0.0.0", 
                port=8005, 
                reload=True)
