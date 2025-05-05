import sys
import random
from pathlib import Path
import time
import asyncio
import uvicorn
from fastapi import Request, Response
from ray import serve

from kodosumi.core import ServeAPI, Launch, Tracer
from kodosumi.service.inout.forms import *
from kodosumi.service.inout.errors import InputsError
from kodosumi import response

app = ServeAPI()

model = Model(
    Markdown("# Test Enter 1"),
    Break(),
    InputText(label="Seconds to run", name="runtime", placeholder="10"),
    InputText(label="Results", name="results", placeholder="3"),
    InputText(label="Stdout", name="stdio", placeholder="30"),
    Submit("Submit"),
    Cancel("Cancel")
)

def lore(n):
    ipsum = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum."
    return "\n".join([ipsum] * n)

async def runner(inputs: dict, tracer: Tracer) -> dict:
    await tracer.markdown("_starting up_")
    t0 = time.time()
    runtime = int(inputs.get("runtime", 10) or 10)
    results = int(inputs.get("results", 3) or 3)
    stdio = int(inputs.get("stdio", 30) or 30)
    r = 0
    s = 0
    await tracer.markdown(
        f"running for {runtime} seconds, {results} results, "
        f"{stdio} stdout messages")
    last_result = None
    last_stdout = None
    while time.time() - t0 < runtime:
        if last_result is None or last_result + runtime / results < time.time():
            last_result = time.time()
            r += 1
            await tracer.markdown(f"#### result: {r}")
            ipsum = lore(random.randint(1, 10))
            print(f"ipsum: {ipsum}")
            await tracer.markdown(f"{ipsum}")
        if last_stdout is None or last_stdout + runtime / stdio < time.time():
            last_stdout = time.time()
            s += 1
            print(f"stdout: {s}")
        await asyncio.sleep(0)
    return response.Markdown(f"# Result\n"
                             f"* finished in {time.time() - t0} seconds\n"
                             f"* {r} results\n"
                             f"* {s} stdout messages")

@app.enter("/", 
           model, 
           summary="Test Enter 1", 
           description="This is a highly simple test.",
           tags=["Test"])
async def enter(request: Request, inputs: dict) -> Response:
    return Launch(request, runner, inputs)

@serve.deployment
@serve.ingress(app)
class TestModel1: pass

fast_app1 = TestModel1.bind()  # type: ignore


if __name__ == "__main__":
    import sys
    from pathlib import Path

    import uvicorn
    sys.path.append(str(Path(__file__).parent.parent))
    uvicorn.run("apps.example8:app", host="0.0.0.0", port=8009, reload=True)
